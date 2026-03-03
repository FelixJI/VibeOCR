"""测试 OCR 工作线程"""

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal, Slot

# 添加项目路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)


class TestWorker(QObject):
    """测试用的工作器"""

    # 输出信号
    finished = Signal(str)
    ready = Signal()

    # 输入信号
    request_work = Signal(str)

    def __init__(self):
        super().__init__()
        self._initialized = False
        # 连接内部信号
        self.request_work.connect(self._do_work)
        logger.info("[TestWorker] 创建完成")

    def _do_work(self, data: str):
        logger.info(f"[TestWorker] 收到工作请求: {data}")
        self._ensure_initialized()
        logger.info(f"[TestWorker] 处理完成: {data}")
        self.finished.emit(f"结果: {data}")

    def _ensure_initialized(self):
        if self._initialized:
            return
        logger.info("[TestWorker] 初始化中...")
        self._initialized = True
        logger.info("[TestWorker] 初始化完成")

    @Slot()
    def on_thread_started(self):
        logger.info("[TestWorker] 线程已启动")
        self.ready.emit()


def test_basic_thread():
    """测试基本的 QThread 工作流程"""
    logger.info("=" * 50)
    logger.info("测试 1: 基本 QThread 工作流程")
    logger.info("=" * 50)

    app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    thread = QThread()
    thread.setObjectName("TestThread")
    worker = TestWorker()
    worker.moveToThread(thread)

    results = []

    def on_finished(result):
        logger.info(f"[主线程] 收到结果: {result}")
        results.append(result)

    def on_ready():
        logger.info("[主线程] 工作线程就绪")
        # 发送工作请求
        worker.request_work.emit("测试数据")

    worker.finished.connect(on_finished)
    worker.ready.connect(on_ready)
    thread.started.connect(worker.on_thread_started)

    logger.info("[主线程] 启动线程...")
    thread.start()

    # 等待结果
    from PySide6.QtCore import QTimer

    timeout_count = [0]

    def check_result():
        if results:
            logger.info("[主线程] 测试成功!")
            app.quit()
        else:
            timeout_count[0] += 1
            if timeout_count[0] > 50:  # 5秒超时
                logger.error("[主线程] 测试超时!")
                app.quit()
            else:
                QTimer.singleShot(100, check_result)

    QTimer.singleShot(100, check_result)
    app.exec()

    thread.quit()
    thread.wait(2000)

    return len(results) > 0


def test_ocr_service():
    """测试 OCR 服务基本功能"""
    logger.info("=" * 50)
    logger.info("测试 2: OCR 服务基本功能")
    logger.info("=" * 50)

    app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    try:
        logger.info("[主线程] 导入 OCR 服务...")
        from vibeocr.services.ocr_service import OCRPipeline, OCRService

        logger.info("[主线程] 创建 OCR 服务实例...")
        ocr = OCRService()

        logger.info("[主线程] 测试获取 pipeline...")
        pipeline = ocr.get_pipeline(OCRPipeline.OCR)
        logger.info(f"[主线程] Pipeline 类型: {type(pipeline)}")

        logger.info("[主线程] OCR 服务测试成功!")
        return True

    except Exception as e:
        logger.error(f"[主线程] OCR 服务测试失败: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_ocr_in_thread():
    """测试在线程中使用 OCR 服务"""
    logger.info("=" * 50)
    logger.info("测试 3: 在工作线程中使用 OCR 服务")
    logger.info("=" * 50)

    app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    class OCRTestWorker(QObject):
        finished = Signal(bool, str)
        ready = Signal()
        request_init = Signal()

        def __init__(self):
            super().__init__()
            self.request_init.connect(self._do_init)
            logger.info("[OCRTestWorker] 创建完成")

        def _do_init(self):
            logger.info("[OCRTestWorker] 开始初始化 OCR 服务...")
            try:
                from vibeocr.services.ocr_service import OCRPipeline, OCRService

                ocr = OCRService()
                pipeline = ocr.get_pipeline(OCRPipeline.OCR)
                logger.info(f"[OCRTestWorker] Pipeline 获取成功: {type(pipeline)}")
                self.finished.emit(True, "OCR 初始化成功")
            except Exception as e:
                logger.error(f"[OCRTestWorker] 初始化失败: {e}")
                self.finished.emit(False, str(e))

        @Slot()
        def on_thread_started(self):
            logger.info("[OCRTestWorker] 线程已启动")
            self.ready.emit()

    thread = QThread()
    thread.setObjectName("OCRTestThread")
    worker = OCRTestWorker()
    worker.moveToThread(thread)

    results = []

    def on_finished(success, msg):
        logger.info(f"[主线程] 收到结果: success={success}, msg={msg}")
        results.append((success, msg))
        app.quit()

    def on_ready():
        logger.info("[主线程] 工作线程就绪，发送初始化请求...")
        worker.request_init.emit()

    worker.finished.connect(on_finished)
    worker.ready.connect(on_ready)
    thread.started.connect(worker.on_thread_started)

    logger.info("[主线程] 启动线程...")
    thread.start()

    # 超时处理
    from PySide6.QtCore import QTimer

    timeout_count = [0]

    def check_timeout():
        if not results:
            timeout_count[0] += 1
            if timeout_count[0] > 100:  # 10秒超时
                logger.error("[主线程] 测试超时!")
                app.quit()
            else:
                QTimer.singleShot(100, check_timeout)

    QTimer.singleShot(100, check_timeout)
    app.exec()

    thread.quit()
    thread.wait(2000)

    return results and results[0][0]


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("OCR 工作线程测试脚本")
    print("=" * 60 + "\n")

    # 测试 1: 基本 QThread
    result1 = test_basic_thread()
    print(f"\n测试 1 结果: {'通过' if result1 else '失败'}\n")

    # 测试 2: OCR 服务
    result2 = test_ocr_service()
    print(f"\n测试 2 结果: {'通过' if result2 else '失败'}\n")

    # 测试 3: OCR 在线程中
    result3 = test_ocr_in_thread()
    print(f"\n测试 3 结果: {'通过' if result3 else '失败'}\n")

    print("\n" + "=" * 60)
    print(f"总结果: {'全部通过' if all([result1, result2, result3]) else '部分失败'}")
    print("=" * 60 + "\n")
