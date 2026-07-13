using Microsoft.UI.Xaml.Controls;
using Microsoft.Web.WebView2.Core;
using Windows.Storage.Streams;

namespace VibeOCR.App.Web;

public sealed class PreviewHost : IDisposable
{
    public const string VirtualHost = "app.vibeocr";
    public static readonly Uri StartUri = new($"https://{VirtualHost}/index.html");
    private readonly WebMessageRouter _router;
    private CoreWebView2? _core;

    public PreviewHost(WebMessageRouter router)
    {
        _router = router ?? throw new ArgumentNullException(nameof(router));
    }

    public event Action<WebBridgeProtocolException>? ProtocolViolation;
    public event Action<string>? StateChanged;

    public static bool IsNavigationAllowed(Uri uri) =>
        uri.IsAbsoluteUri &&
        uri.Scheme == Uri.UriSchemeHttps &&
        uri.Host.Equals(VirtualHost, StringComparison.OrdinalIgnoreCase) &&
        uri.IsDefaultPort &&
        string.IsNullOrEmpty(uri.UserInfo) &&
        string.IsNullOrEmpty(uri.Query);

    public async Task InitializeAsync(WebView2 webView, string assetFolder)
    {
        ArgumentNullException.ThrowIfNull(webView);
        ArgumentException.ThrowIfNullOrWhiteSpace(assetFolder);
        if (_core is not null)
        {
            throw new InvalidOperationException("Preview host is already initialized.");
        }

        string assets = Path.GetFullPath(assetFolder);
        string applicationRoot = Path.GetFullPath(AppContext.BaseDirectory);
        if (!assets.StartsWith(applicationRoot, StringComparison.OrdinalIgnoreCase) ||
            !Directory.Exists(assets))
        {
            throw new InvalidOperationException("Preview assets must be packaged under the application root.");
        }

        await webView.EnsureCoreWebView2Async();
        CoreWebView2 core = webView.CoreWebView2;
        core.SetVirtualHostNameToFolderMapping(
            VirtualHost,
            assets,
            CoreWebView2HostResourceAccessKind.DenyCors);
        core.Settings.AreBrowserAcceleratorKeysEnabled = false;
        core.Settings.AreDefaultContextMenusEnabled = false;
        core.Settings.AreDevToolsEnabled = false;
        core.Settings.IsBuiltInErrorPageEnabled = false;
        core.Settings.IsGeneralAutofillEnabled = false;
        core.Settings.IsPasswordAutosaveEnabled = false;
        core.Settings.IsStatusBarEnabled = false;
        core.Settings.IsWebMessageEnabled = true;
        core.NavigationStarting += OnNavigationStarting;
        core.NavigationCompleted += OnNavigationCompleted;
        core.DOMContentLoaded += OnDomContentLoaded;
        core.NewWindowRequested += OnNewWindowRequested;
        core.PermissionRequested += OnPermissionRequested;
        core.DownloadStarting += OnDownloadStarting;
        core.WebMessageReceived += OnWebMessageReceived;
        core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
        core.WebResourceRequested += OnWebResourceRequested;
        _core = core;
        core.Navigate(StartUri.AbsoluteUri);
    }

    private static void OnNavigationStarting(
        CoreWebView2 sender,
        CoreWebView2NavigationStartingEventArgs args)
    {
        if (!Uri.TryCreate(args.Uri, UriKind.Absolute, out Uri? uri) || !IsNavigationAllowed(uri))
        {
            args.Cancel = true;
        }
    }

    private void OnNavigationCompleted(
        CoreWebView2 sender,
        CoreWebView2NavigationCompletedEventArgs args) =>
        StateChanged?.Invoke(args.IsSuccess
            ? "navigation-complete"
            : $"navigation-failed:{args.WebErrorStatus}");

    private void OnDomContentLoaded(
        CoreWebView2 sender,
        CoreWebView2DOMContentLoadedEventArgs args) =>
        StateChanged?.Invoke("dom-content-loaded");

    private static void OnNewWindowRequested(
        CoreWebView2 sender,
        CoreWebView2NewWindowRequestedEventArgs args) => args.Handled = true;

    private static void OnPermissionRequested(
        CoreWebView2 sender,
        CoreWebView2PermissionRequestedEventArgs args)
    {
        args.State = CoreWebView2PermissionState.Deny;
        args.Handled = true;
    }

    private static void OnDownloadStarting(
        CoreWebView2 sender,
        CoreWebView2DownloadStartingEventArgs args) => args.Cancel = true;

    private void OnWebMessageReceived(
        CoreWebView2 sender,
        CoreWebView2WebMessageReceivedEventArgs args)
    {
        try
        {
            _router.Handle(args.WebMessageAsJson, args.Source);
        }
        catch (WebBridgeProtocolException error)
        {
            ProtocolViolation?.Invoke(error);
        }
    }

    private static void OnWebResourceRequested(
        CoreWebView2 sender,
        CoreWebView2WebResourceRequestedEventArgs args)
    {
        if (Uri.TryCreate(args.Request.Uri, UriKind.Absolute, out Uri? uri) &&
            IsNavigationAllowed(uri))
        {
            return;
        }

        args.Response = sender.Environment.CreateWebResourceResponse(
            new InMemoryRandomAccessStream(),
            403,
            "Forbidden",
            "Content-Type: text/plain; charset=utf-8");
    }

    public void Dispose()
    {
        CoreWebView2? core = Interlocked.Exchange(ref _core, null);
        if (core is null)
        {
            return;
        }

        core.NavigationStarting -= OnNavigationStarting;
        core.NavigationCompleted -= OnNavigationCompleted;
        core.DOMContentLoaded -= OnDomContentLoaded;
        core.NewWindowRequested -= OnNewWindowRequested;
        core.PermissionRequested -= OnPermissionRequested;
        core.DownloadStarting -= OnDownloadStarting;
        core.WebMessageReceived -= OnWebMessageReceived;
        core.WebResourceRequested -= OnWebResourceRequested;
        core.ClearVirtualHostNameToFolderMapping(VirtualHost);
    }
}
