# 设备授权运行组件

来自同工作区 `device-license` 的 0.2.0 运行发布，DLL 协议版本 `1.0.0-rsa`。
仅包含客户验证组件及原构建哈希清单，不包含 issuer、签发私钥、请求或许可证。
主板匹配、RSA 签名、产品及协议校验由 DLL 完成；Python 只负责调用及许可证存储。
运行依赖 Windows 的 .NET Framework 4.x；客户版支持 Windows 10/11 x64。
