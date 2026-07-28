using System.Runtime.InteropServices;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Microsoft.UI.Xaml.Shapes;
using VibeOCR.Platform.Windows;
using Windows.Graphics;
using Windows.Storage.Streams;
using Windows.System;

namespace VibeOCR.App.Features.Recognition;

public sealed record ScreenRegionSelection(
    PhysicalRectangle Bounds,
    byte[] Bgra,
    int Stride);

public interface IScreenRegionPicker
{
    Task<ScreenRegionSelection?> PickAsync(CancellationToken cancellationToken);
}

/// <summary>
/// Lightweight region selector for the WinUI screenshot workflow.  It takes a
/// single frozen snapshot after hiding the main window, then crops from that
/// snapshot so the application cannot reappear in the final OCR input.
/// </summary>
public sealed class ScreenRegionPicker(Func<nint> ownerWindow) : IScreenRegionPicker
{
    private const long MaximumCaptureBytes = 256L << 20;
    private const int VirtualScreenX = 76;
    private const int VirtualScreenY = 77;
    private const int VirtualScreenWidth = 78;
    private const int VirtualScreenHeight = 79;
    private readonly Func<nint> _ownerWindow = ownerWindow ?? throw new ArgumentNullException(nameof(ownerWindow));

    public async Task<ScreenRegionSelection?> PickAsync(CancellationToken cancellationToken)
    {
        PhysicalRectangle desktop = GetVirtualDesktop();
        nint owner = _ownerWindow();
        ShowWindow(owner, 0);
        // 暂时强制任务栏自动隐藏，否则它会盖住截图遮罩顶部或因置顶而闪现。
        // 保存原状态，在 finally 中恢复，避免改变用户的任务栏偏好。
        AppBarStateScope taskbarState = HideTaskbars();
        try
        {
            await Task.Delay(180, cancellationToken);
            await using var capture = new ScreenCaptureService(Guid.NewGuid());
            CapturedFrame frame = capture.Capture(desktop, TimeSpan.FromMinutes(1));
            byte[] desktopBgra = capture.Read(frame);
            byte[] desktopBmp = EncodeTopDownBmp(
                desktopBgra,
                desktop.Width,
                desktop.Height,
                frame.Stride);
            BitmapImage background = await LoadBitmapAsync(
                desktopBmp,
                desktop.Width,
                desktop.Height);
            PhysicalRectangle? selected = await ShowOverlayAsync(
                desktop,
                background,
                cancellationToken);
            if (selected is null)
            {
                return null;
            }

            byte[] cropped = CropBgra(desktopBgra, desktop, selected.Value);
            return new ScreenRegionSelection(
                selected.Value,
                cropped,
                selected.Value.Width * 4);
        }
        finally
        {
            taskbarState.Dispose();
            ShowWindow(owner, 9);
            SetForegroundWindow(owner);
        }
    }

    /// <summary>
    /// Enumerate the primary taskbar (plus any secondary monitor Shell tray
    /// windows) and force them into auto-hide for the duration of the screenshot
    /// overlay, returning a scope that restores the prior state on dispose.
    /// </summary>
    private static AppBarStateScope HideTaskbars()
    {
        var saved = new List<(nint hWnd, uint State)>();
        nint taskbar = FindWindow("Shell_TrayWnd", null);
        if (taskbar != nint.Zero)
        {
            saved.Add((taskbar, GetTaskbarState(taskbar)));
            SetTaskbarState(taskbar, AbsAutohide | AbsAlwaysOnTop);
        }

        // Secondary-monitor taskbars live in windows of class "Shell_SecondaryTrayWnd".
        nint secondary = nint.Zero;
        while ((secondary = FindWindowEx(nint.Zero, secondary, "Shell_SecondaryTrayWnd", null)) != nint.Zero)
        {
            saved.Add((secondary, GetTaskbarState(secondary)));
            SetTaskbarState(secondary, AbsAutohide | AbsAlwaysOnTop);
        }

        return new AppBarStateScope(saved);
    }

    private static uint GetTaskbarState(nint taskbar)
    {
        var data = new AppBarData { cbSize = (uint)Marshal.SizeOf<AppBarData>(), hWnd = taskbar };
        return (uint)SHAppBarMessage(AbmGetState, ref data);
    }

    private static void SetTaskbarState(nint taskbar, uint state)
    {
        var data = new AppBarData
        {
            cbSize = (uint)Marshal.SizeOf<AppBarData>(),
            hWnd = taskbar,
            lParam = (nint)(int)state,
        };
        SHAppBarMessage(AbmSetState, ref data);
    }

    private static async Task<PhysicalRectangle?> ShowOverlayAsync(
        PhysicalRectangle desktop,
        BitmapImage background,
        CancellationToken cancellationToken)
    {
        var completion = new TaskCompletionSource<PhysicalRectangle?>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        var overlay = new Window();
        var root = new Grid
        {
            Background = new SolidColorBrush(Windows.UI.Color.FromArgb(1, 0, 0, 0)),
        };
        root.Children.Add(new Image { Source = background, Stretch = Stretch.Fill });
        root.Children.Add(new Border
        {
            Background = new SolidColorBrush(Windows.UI.Color.FromArgb(72, 0, 0, 0)),
        });
        var canvas = new Canvas
        {
            Background = new SolidColorBrush(Windows.UI.Color.FromArgb(1, 0, 0, 0)),
        };
        var selection = new Rectangle
        {
            Stroke = new SolidColorBrush(Windows.UI.Color.FromArgb(255, 255, 255, 255)),
            StrokeThickness = 2,
            Fill = new SolidColorBrush(Windows.UI.Color.FromArgb(22, 255, 255, 255)),
            Visibility = Visibility.Collapsed,
        };
        canvas.Children.Add(selection);
        root.Children.Add(canvas);
        root.Children.Add(new Border
        {
            Margin = new Thickness(20),
            Padding = new Thickness(12, 8, 12, 8),
            HorizontalAlignment = HorizontalAlignment.Left,
            VerticalAlignment = VerticalAlignment.Top,
            Background = new SolidColorBrush(Windows.UI.Color.FromArgb(210, 24, 24, 24)),
            CornerRadius = new CornerRadius(6),
            Child = new TextBlock
            {
                Text = "拖动框选识别区域 · 右键重新框选 / 取消 · Esc 取消",
                Foreground = new SolidColorBrush(Windows.UI.Color.FromArgb(255, 255, 255, 255)),
            },
        });
        overlay.Content = root;

        Windows.Foundation.Point? start = null;
        canvas.PointerPressed += (_, args) =>
        {
            var properties = args.GetCurrentPoint(canvas).Properties;

            // 右键按状态分支：已有框选→清空重选；空状态→取消并关闭遮罩。
            if (properties.IsRightButtonPressed)
            {
                if (start is not null)
                {
                    // 兜底：拖拽进行中收到右键（左键 capture 时一般不触发，但保险）。
                    start = null;
                    canvas.ReleasePointerCapture(args.Pointer);
                }

                if (selection.Visibility == Visibility.Visible)
                {
                    selection.Visibility = Visibility.Collapsed;
                    selection.Width = 0;
                    selection.Height = 0;
                }
                else
                {
                    completion.TrySetResult(null);
                    overlay.Close();
                }
                args.Handled = true;
                return;
            }

            if (!properties.IsLeftButtonPressed)
            {
                return;
            }

            start = args.GetCurrentPoint(canvas).Position;
            selection.Visibility = Visibility.Visible;
            selection.Width = 0;
            selection.Height = 0;
            canvas.CapturePointer(args.Pointer);
            args.Handled = true;
        };
        canvas.PointerMoved += (_, args) =>
        {
            if (start is not { } origin || !args.GetCurrentPoint(canvas).Properties.IsLeftButtonPressed)
            {
                return;
            }

            UpdateSelection(selection, origin, args.GetCurrentPoint(canvas).Position);
        };
        canvas.PointerReleased += (_, args) =>
        {
            if (start is not { } origin)
            {
                return;
            }

            Windows.Foundation.Point end = args.GetCurrentPoint(canvas).Position;
            UpdateSelection(selection, origin, end);
            start = null;
            canvas.ReleasePointerCapture(args.Pointer);
            double left = Math.Min(origin.X, end.X);
            double top = Math.Min(origin.Y, end.Y);
            double width = Math.Abs(end.X - origin.X);
            double height = Math.Abs(end.Y - origin.Y);
            if (width >= 4 && height >= 4 && canvas.ActualWidth > 0 && canvas.ActualHeight > 0)
            {
                completion.TrySetResult(ScaleSelection(
                    desktop,
                    left,
                    top,
                    width,
                    height,
                    canvas.ActualWidth,
                    canvas.ActualHeight));
                overlay.Close();
            }
            else
            {
                // 选区太小：清空并留在遮罩等待重新框选，替代原来的静默保留。
                selection.Visibility = Visibility.Collapsed;
                selection.Width = 0;
                selection.Height = 0;
            }
        };
        var keyboardSink = new Button
        {
            Width = 1,
            Height = 1,
            Opacity = 0,
            IsTabStop = true,
            HorizontalAlignment = HorizontalAlignment.Left,
            VerticalAlignment = VerticalAlignment.Top,
        };
        root.Children.Add(keyboardSink);
        root.KeyDown += (_, args) =>
        {
            if (args.Key == VirtualKey.Escape)
            {
                completion.TrySetResult(null);
                overlay.Close();
                args.Handled = true;
            }
        };
        overlay.Closed += (_, _) => completion.TrySetResult(null);

        OverlappedPresenter presenter = OverlappedPresenter.Create();
        presenter.SetBorderAndTitleBar(false, false);
        presenter.IsAlwaysOnTop = true;
        presenter.IsResizable = false;
        overlay.AppWindow.SetPresenter(presenter);
        overlay.AppWindow.IsShownInSwitchers = false;
        overlay.AppWindow.MoveAndResize(new RectInt32(
            desktop.X,
            desktop.Y,
            desktop.Width,
            desktop.Height));
        overlay.Activate();
        keyboardSink.Focus(FocusState.Programmatic);

        using CancellationTokenRegistration registration = cancellationToken.Register(() =>
            root.DispatcherQueue.TryEnqueue(() =>
            {
                completion.TrySetCanceled(cancellationToken);
                overlay.Close();
            }));
        return await completion.Task;
    }

    public static PhysicalRectangle ScaleSelection(
        PhysicalRectangle desktop,
        double left,
        double top,
        double width,
        double height,
        double canvasWidth,
        double canvasHeight)
    {
        int x = desktop.X + (int)Math.Round(left * desktop.Width / canvasWidth);
        int y = desktop.Y + (int)Math.Round(top * desktop.Height / canvasHeight);
        int right = desktop.X + (int)Math.Round((left + width) * desktop.Width / canvasWidth);
        int bottom = desktop.Y + (int)Math.Round((top + height) * desktop.Height / canvasHeight);
        return new PhysicalRectangle(x, y, Math.Max(1, right - x), Math.Max(1, bottom - y));
    }

    private static void UpdateSelection(
        Rectangle selection,
        Windows.Foundation.Point start,
        Windows.Foundation.Point end)
    {
        Canvas.SetLeft(selection, Math.Min(start.X, end.X));
        Canvas.SetTop(selection, Math.Min(start.Y, end.Y));
        selection.Width = Math.Abs(end.X - start.X);
        selection.Height = Math.Abs(end.Y - start.Y);
    }

    private static byte[] CropBgra(
        byte[] source,
        PhysicalRectangle desktop,
        PhysicalRectangle selected)
    {
        int sourceStride = checked(desktop.Width * 4);
        int targetStride = checked(selected.Width * 4);
        byte[] cropped = new byte[checked(targetStride * selected.Height)];
        int offsetX = selected.X - desktop.X;
        int offsetY = selected.Y - desktop.Y;
        for (int row = 0; row < selected.Height; row++)
        {
            System.Buffer.BlockCopy(
                source,
                checked((offsetY + row) * sourceStride + offsetX * 4),
                cropped,
                row * targetStride,
                targetStride);
        }

        return cropped;
    }

    private static async Task<BitmapImage> LoadBitmapAsync(byte[] data, int physicalWidth, int physicalHeight)
    {
        var stream = new InMemoryRandomAccessStream();
        using (var writer = new DataWriter(stream))
        {
            writer.WriteBytes(data);
            await writer.StoreAsync();
            writer.DetachStream();
        }
        stream.Seek(0);
        var bitmap = new BitmapImage
        {
            // Pin the decode size to the captured physical pixels and interpret it
            // in physical units. Without this, WinUI 3 defaults to
            // DecodePixelType=Logical and auto-scales the decoded bitmap to the
            // effective/logical layout size — so on a >100% DPI display the frozen
            // desktop backdrop is downsampled then stretched back by Stretch.Fill,
            // producing the blurry overlay the screenshot workflow used to show.
            DecodePixelType = DecodePixelType.Physical,
            DecodePixelWidth = physicalWidth,
            DecodePixelHeight = physicalHeight,
        };
        await bitmap.SetSourceAsync(stream);
        return bitmap;
    }

    private static byte[] EncodeTopDownBmp(byte[] bgra, int width, int height, int stride)
    {
        int pixelBytes = checked(stride * height);
        byte[] bmp = new byte[checked(54 + pixelBytes)];
        bmp[0] = (byte)'B';
        bmp[1] = (byte)'M';
        BitConverter.GetBytes(bmp.Length).CopyTo(bmp, 2);
        BitConverter.GetBytes(54).CopyTo(bmp, 10);
        BitConverter.GetBytes(40).CopyTo(bmp, 14);
        BitConverter.GetBytes(width).CopyTo(bmp, 18);
        BitConverter.GetBytes(-height).CopyTo(bmp, 22);
        BitConverter.GetBytes((short)1).CopyTo(bmp, 26);
        BitConverter.GetBytes((short)32).CopyTo(bmp, 28);
        BitConverter.GetBytes(pixelBytes).CopyTo(bmp, 34);
        bgra.CopyTo(bmp, 54);
        return bmp;
    }

    private static PhysicalRectangle GetVirtualDesktop()
    {
        var desktop = new PhysicalRectangle(
            GetSystemMetrics(VirtualScreenX),
            GetSystemMetrics(VirtualScreenY),
            GetSystemMetrics(VirtualScreenWidth),
            GetSystemMetrics(VirtualScreenHeight));
        desktop.Validate();
        if (checked((long)desktop.Width * desktop.Height * 4) > MaximumCaptureBytes)
        {
            throw new InvalidDataException("虚拟桌面截图超过 256 MiB 限制。");
        }
        return desktop;
    }

    [DllImport("user32.dll")]
    private static extern int GetSystemMetrics(int index);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool ShowWindow(nint window, int command);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetForegroundWindow(nint window);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern nint FindWindow(string? lpClassName, string? lpWindowName);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern nint FindWindowEx(nint hWndParent, nint hWndChildAfter, string? lpszClass, string? lpszWindow);

    [DllImport("shell32.dll", CallingConvention = CallingConvention.StdCall)]
    private static extern nint SHAppBarMessage(uint dwMessage, ref AppBarData pData);

    private const uint AbmGetState = 4;
    private const uint AbmSetState = 10;
    private const uint AbsAutohide = 0x0000001;
    private const uint AbsAlwaysOnTop = 0x0000002;

    [StructLayout(LayoutKind.Sequential)]
    private struct AppBarData
    {
        public uint cbSize;
        public nint hWnd;
        public uint uCallbackMessage;
        public uint uEdge;
        public Rect rc;
        public nint lParam;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct Rect
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    /// <summary>
    /// Restores the saved taskbar auto-hide states on dispose. Holds the saved
    /// (hWnd, originalState) pairs captured before the screenshot overlay forced
    /// them into auto-hide.
    /// </summary>
    private sealed class AppBarStateScope : IDisposable
    {
        private List<(nint HWnd, uint State)>? _saved;
        private bool _disposed;

        public AppBarStateScope(List<(nint HWnd, uint State)> saved) => _saved = saved;

        public void Dispose()
        {
            if (_disposed)
            {
                return;
            }

            _disposed = true;
            if (_saved is null)
            {
                return;
            }

            foreach ((nint hWnd, uint state) in _saved)
            {
                SetTaskbarState(hWnd, state);
            }

            _saved = null;
        }
    }
}
