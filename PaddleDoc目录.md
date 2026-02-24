# PaddleOCR 文档目录

本文档是对 `paddle_doc` 目录内容的结构化总结，便于快速查找和使用相关文档。

---

## 📁 根目录文件

### 快速入门
- **index.md** - 主索引文档（包含快速开始、安装、基础使用等内容）
- **index.en.md** - 英文版主索引文档

### 常见问题与更新日志
- **FAQ.md** / **FAQ.en.md** - 常见问题解答
- **CHANGELOG.md** / **CHANGELOG.en.md** - 版本更新日志
- **VisualDL.md** / **VisualDL.en.md** - VisualDL 可视化工具文档

### API 变更记录
- **API_change_log/v3.0.0rc.md** / **v3.0.0rc.en.md** - v3.0.0rc 版本 API 变更记录

---

## 📦 安装指南 (`installation/`)

- **installation.md** / **installation.en.md** - PaddlePaddle 安装指南
- **paddlepaddle_install.md** / **paddlepaddle_install.en.md** - PaddlePaddle 详细安装说明

---

## 🏷️ 数据标注指南 (`data_annotations/`)

### CV 模块标注 (`cv_modules/`)
| 文档 | 说明 |
|------|------|
| image_classification | 图像分类数据标注 |
| image_feature | 图像特征提取标注 |
| instance_segmentation | 实例分割标注 |
| keypoint_detection | 关键点检测标注 |
| ml_classification | 机器学习分类标注 |
| object_detection | 目标检测标注 |
| semantic_segmentation | 语义分割标注 |

### OCR 模块标注 (`ocr_modules/`)
| 文档 | 说明 |
|------|------|
| table_recognition | 表格识别数据标注 |
| text_detection_recognition | 文本检测与识别标注 |

### 时序模块标注 (`time_series_modules/`)
| 文档 | 说明 |
|------|------|
| time_series_anomaly_detection | 时序异常检测标注 |
| time_series_classification | 时序分类标注 |
| time_series_forecasting | 时序预测标注 |

### 视频模块标注 (`video_modules/`)
| 文档 | 说明 |
|------|------|
| video_classification | 视频分类标注 |
| video_detection | 视频检测标注 |

---

## 🧩 模型使用教程 (`module_usage/`)

### 使用说明 (`instructions/`)
| 文档 | 说明 |
|------|------|
| benchmark | 性能基准测试 |
| config_parameters_common | 通用配置参数说明 |
| config_parameters_3d | 3D 任务配置参数 |
| config_parameters_time_series | 时序任务配置参数 |
| distributed_training | 分布式训练指南 |
| model_python_API | 模型 Python API 文档 |

### CV 模块教程 (`tutorials/cv_modules/`)
| 文档 | 说明 |
|------|------|
| 3d_bev_detection | 3D 鸟瞰图检测 |
| anomaly_detection | 异常检测 |
| face_detection | 人脸检测 |
| face_feature | 人脸特征提取 |
| human_detection | 人体检测 |
| human_keypoint_detection | 人体关键点检测 |
| image_classification | 图像分类 |
| image_feature | 图像特征 |
| image_multilabel_classification | 图像多标签分类 |
| instance_segmentation | 实例分割 |
| mainbody_detection | 主体检测 |
| object_detection | 目标检测 |
| open_vocabulary_detection | 开放词汇检测 |
| open_vocabulary_segmentation | 开放词汇分割 |
| pedestrian_attribute_recognition | 行人属性识别 |
| rotated_object_detection | 旋转目标检测 |
| semantic_segmentation | 语义分割 |
| small_object_detection | 小目标检测 |
| vehicle_attribute_recognition | 车辆属性识别 |
| vehicle_detection | 车辆检测 |

### OCR 模块教程 (`tutorials/ocr_modules/`)
| 文档 | 说明 |
|------|------|
| doc_img_orientation_classification | 文档图像方向分类 |
| formula_recognition | 公式识别 |
| layout_detection | 版面分析 |
| seal_text_detection | 印章文本检测 |
| table_cells_detection | 表格单元格检测 |
| table_classification | 表格分类 |
| table_structure_recognition | 表格结构识别 |
| textline_orientation_classification | 文本行方向分类 |
| text_detection | 文本检测 |
| text_image_unwarping | 文本图像矫正 |
| text_recognition | 文本识别 |

### 语音模块教程 (`tutorials/speech_modules/`)
| 文档 | 说明 |
|------|------|
| multilingual_speech_recognition | 多语言语音识别 |
| text_to_pinyin | 文本转拼音 |
| text_to_speech_acoustic | 文本转语音（声学模型） |
| text_to_speech_vocoder | 文本转语音（声码器） |

### 时序模块教程 (`tutorials/time_series_modules/`)
| 文档 | 说明 |
|------|------|
| time_series_anomaly_detection | 时序异常检测 |
| time_series_classification | 时序分类 |
| time_series_forecasting | 时序预测 |

### 视频模块教程 (`tutorials/video_modules/`)
| 文档 | 说明 |
|------|------|
| video_classification | 视频分类 |
| video_detection | 视频检测 |

### VLM 模块教程 (`tutorials/vlm_modules/`)
| 文档 | 说明 |
|------|------|
| chart_parsing | 图表解析 |
| doc_vlm | 文档视觉语言模型 |

---

## 🔄 管道使用教程 (`pipeline_usage/`)

### 使用说明 (`instructions/`)
| 文档 | 说明 |
|------|------|
| benchmark | 性能基准测试 |
| parallel_inference | 并行推理 |
| pipeline_CLI_usage | 管道命令行使用 |
| pipeline_python_API | 管道 Python API |
| pipeline_develop_guide | 管道开发指南 |

### CV 管道教程 (`tutorials/cv_pipelines/`)
| 文档 | 说明 |
|------|------|
| 3d_bev_detection | 3D 鸟瞰图检测 |
| face_recognition | 人脸识别 |
| general_image_recognition | 通用图像识别 |
| human_keypoint_detection | 人体关键点检测 |
| image_anomaly_detection | 图像异常检测 |
| image_classification | 图像分类 |
| image_multi_label_classification | 图像多标签分类 |
| instance_segmentation | 实例分割 |
| object_detection | 目标检测 |
| open_vocabulary_detection | 开放词汇检测 |
| open_vocabulary_segmentation | 开放词汇分割 |
| pedestrian_attribute_recognition | 行人属性识别 |
| rotated_object_detection | 旋转目标检测 |
| semantic_segmentation | 语义分割 |
| small_object_detection | 小目标检测 |
| vehicle_attribute_recognition | 车辆属性识别 |

### OCR 管道教程 (`tutorials/ocr_pipelines/`)
| 文档 | 说明 |
|------|------|
| OCR | OCR 系统介绍 |
| PaddleOCR-VL | PaddleOCR 视觉语言模型 |
| PP-DocTranslation | 文档翻译 |
| PP-StructureV3 | 文档结构解析 V3 |
| doc_preprocessor | 文档预处理 |
| formula_recognition | 公式识别 |
| layout_parsing | 版面解析 |
| seal_recognition | 印章识别 |
| table_recognition | 表格识别 |
| table_recognition_v2 | 表格识别 V2 |

### 信息抽取管道 (`tutorials/information_extraction_pipelines/`)
| 文档 | 说明 |
|------|------|
| document_scene_information_extraction_v3 | 文档场景信息抽取 V3 |
| document_scene_information_extraction_v4 | 文档场景信息抽取 V4 |

### 语音管道 (`tutorials/speech_pipelines/`)
| 文档 | 说明 |
|------|------|
| multilingual_speech_recognition | 多语言语音识别 |
| text_to_speech | 文本转语音 |

### 时序管道 (`tutorials/time_series_pipelines/`)
| 文档 | 说明 |
|------|------|
| time_series_anomaly_detection | 时序异常检测 |
| time_series_classification | 时序分类 |
| time_series_forecasting | 时序预测 |

### 视频管道 (`tutorials/video_pipelines/`)
| 文档 | 说明 |
|------|------|
| video_classification | 视频分类 |
| video_detection | 视频检测 |

### VLM 管道 (`tutorials/vlm_pipelines/`)
| 文档 | 说明 |
|------|------|
| doc_understanding | 文档理解 |

---

## 🚀 管道部署 (`pipeline_deploy/`)

| 文档 | 说明 |
|------|------|
| high_performance_inference | 高性能推理 |
| on_device_deployment | 端侧部署 |
| packaging | 打包发布 |
| paddle2onnx | Paddle 转 ONNX |
| serving | 模型服务化部署 |

---

## 🛠️ 其他设备支持 (`other_devices_support/`)

### 贡献指南
- **how_to_contribute_device.md** - 如何贡献设备支持
- **how_to_contribute_model.md** - 如何贡献模型

### 多设备使用
- **multi_devices_use_guide.md** - 多设备使用指南

### 特定硬件安装
| 文档 | 硬件 |
|------|------|
| paddlepaddle_install_DCU | DCU（海光） |
| paddlepaddle_install_GCU | GCU（天数智芯） |
| paddlepaddle_install_MLU | MLU（寒武纪） |
| paddlepaddle_install_NPU | NPU（华为昇腾） |
| paddlepaddle_install_XPU | XPU（百度昆仑） |

---

## 📚 实战教程 (`practical_tutorials/`)

### CV 实战
| 文档 | 说明 |
|------|------|
| anomaly_detection_tutorial | 异常检测实战 |
| face_recognition_tutorial | 人脸识别实战 |
| image_classification_garbage_tutorial | 垃圾分类实战 |
| instance_segmentation_remote_sensing_tutorial | 遥感实例分割实战 |
| object_detection_fall_tutorial | 跌倒检测实战 |
| object_detection_fashion_pedia_tutorial | 时尚检测实战 |
| semantic_segmentation_road_tutorial | 道路分割实战 |
| small_object_detection_tutorial | 小目标检测实战 |

### OCR 实战
| 文档 | 说明 |
|------|------|
| formula_recognition_tutorial | 公式识别实战 |
| layout_detection | 版面分析实战 |
| ocr_det_license_tutorial | 车牌检测实战 |
| ocr_rec_chinese_tutorial | 中文识别实战 |
| table_recognition_v2_tutorial | 表格识别实战 |

### 信息抽取实战
| 文档 | 说明 |
|------|------|
| document_scene_information_extraction(deepseek)_tutorial | 基于 DeepSeek 的信息抽取 |
| document_scene_information_extraction(layout_detection)_tutorial | 版面检测信息抽取 |
| document_scene_information_extraction(seal_recognition)_tutorial | 印章识别信息抽取 |

### 时序实战
| 文档 | 说明 |
|------|------|
| ts_anomaly_detection | 时序异常检测实战 |
| ts_classification | 时序分类实战 |
| ts_forecast | 时序预测实战 |

### 部署实战
| 文档 | 说明 |
|------|------|
| deployment_tutorial | 部署教程 |
| high_performance_npu_tutorial | NPU 高性能教程 |

---

## 📋 支持列表 (`support_list/`)

### 模型支持列表
| 文档 | 说明 |
|------|------|
| models_list | 模型支持列表 |
| model_list_dcu | DCU 支持模型列表 |
| model_list_gcu | GCU 支持模型列表 |
| model_list_mlu | MLU 支持模型列表 |
| model_list_npu | NPU 支持模型列表 |
| model_list_xpu | XPU 支持模型列表 |

### 管道支持列表
| 文档 | 说明 |
|------|------|
| pipelines_list | 管道支持列表 |
| pipelines_list_dcu | DCU 支持管道列表 |
| pipelines_list_gcu | GCU 支持管道列表 |
| pipelines_list_mlu | MLU 支持管道列表 |
| pipelines_list_npu | NPU 支持管道列表 |
| pipelines_list_xpu | XPU 支持管道列表 |

---

## 📌 快速导航

### OCR 相关
- **快速开始**: `index.md`
- **文本检测**: `module_usage/tutorials/ocr_modules/text_detection.md`
- **文本识别**: `module_usage/tutorials/ocr_modules/text_recognition.md`
- **表格识别**: `module_usage/tutorials/ocr_modules/table_recognition.md`
- **公式识别**: `module_usage/tutorials/ocr_modules/formula_recognition.md`
- **版面分析**: `module_usage/tutorials/ocr_modules/layout_detection.md`
- **OCR 管道**: `pipeline_usage/tutorials/ocr_pipelines/OCR.md`

### 部署相关
- **性能优化**: `pipeline_deploy/high_performance_inference.md`
- **ONNX 转换**: `pipeline_deploy/paddle2onnx.md`
- **服务化部署**: `pipeline_deploy/serving.md`

### 数据标注
- **CV 标注**: `data_annotations/cv_modules/`
- **OCR 标注**: `data_annotations/ocr_modules/`

---

*最后更新: 2025-02-23*
