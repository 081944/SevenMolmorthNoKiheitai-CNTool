# 7W ITE 图片逆向文档索引

ITE 逆向结果现拆分为两份文档，避免格式规范与单次定位过程混在一起：

- `docs/ITE图片格式与编解码说明.md`
  - FHN 和 ITE 容器结构
  - `64x32` tile、type 0/type 2 和共享空白 tile
  - PS2 IPU IDEC/MPEG-2 intra 位流与 color/mask 宏块布局
  - PNG 提取、ITE 重建、FHN 写回命令及安全限制
- `docs/ITE图片定位与调试方法.md`
  - 从 PCSX2 “ネーミング”界面定位到 `name.fhn` ITE 12 的完整证据链
  - GS dump、EE 内存、IDA/DebugServer 和离线解码各自的用途
  - 后续类似图片的候选筛选、运行时确认和明显修改验证流程

当前状态：离线的“FHN -> PNG -> 修改 -> ITE -> FHN -> PNG”闭环已经通过；
修改后的 FHN 尚待重打包 CDIMAGE 后由 PCSX2 冷启动进行最终游戏兼容性验证。
