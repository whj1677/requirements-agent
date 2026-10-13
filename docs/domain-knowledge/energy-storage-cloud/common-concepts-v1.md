# 储能云平台基础概念

2026-10-12。用于需求 Agent 的共用领域背景，随适用场景的首次输入完整提供。以下是通用概念参考；云平台采用本知识包的工作定义。它们不证明本公司平台已经实现某项能力，不规定本期需求范围。

## 直接注入的内容

【G-001｜BMS】BMS = Battery Management System，电池管理系统。面向电池进行状态监测和管理，常见职责包括电压、电流、温度采集，SOC/SOH 估算，以及保护和均衡相关管理。云平台读取 BMS 数据，不等于云平台承担电池现场保护。[资料 1，第 2–5 页]

【G-002｜PCS】PCS = Power Conversion System，储能变流系统，常称储能变流器。在典型交流并网电池储能中，连接电池直流侧与交流系统，完成双向电能转换并控制充放电功率。功率指令与实际反馈需要分开，PCS 也不等于整套储能系统。[资料 2，第 2–3 页；资料 1，第 6 页]

【G-003｜EMS】EMS = Energy Management System，能源管理系统。综合运行状态、目标和约束，协调设备运行及充放电安排；可在站端或集中侧部署。EMS 是能源管理功能或系统，不与云平台完全等同，不能预设所有策略都在云端计算。[资料 1，第 1–2 页]

【G-004｜SOC】SOC = State of Charge，荷电状态。表示电池在约定基准和估算口径下的荷电程度，通常用百分比显示，是状态指标而非独立设备。SOC 50% 不代表必然能对外放出额定能量的 50%；可用能量还受运行范围、退化和工况等影响。[资料 1，第 5 页；资料 3，第 3–5 页]

【G-005｜SOH】SOH = State of Health，健康状态。表示电池相对于选定基准的健康或退化程度，也是状态指标；容量、内阻等可作为不同表征。当前容量与基准容量之比是常见口径之一，不统一指定算法、报废阈值或剩余寿命。[资料 1，第 5 页]

【G-006｜储能云平台】本知识包将储能云平台理解为：汇集站点运行数据，支撑远程监测、运维协作和业务分析的软件平台；部分产品还支持策略管理或远程调控。这是便于需求分析的工作定义，不是统一标准定义。本公司当前部署能力需结合源码观察、部署信息和当前项目资料核对，不能仅凭名称推定云平台替代站端 EMS、直接连接每个设备或具有全部控制权。

【G-007｜彼此关系】典型关系可理解为：电池储存能量，通过充放电吸收或输出电能，BMS 监测和管理电池；PCS 承担电池与交流系统之间的电能转换；EMS 利用电池/PCS 等状态协调运行；SOC/SOH 描述电池状态，常见由 BMS 估算并被上层使用。云平台通过项目实际的采集与通信链路汇总相关数据，可支撑跨站点管理；是否参与调控以及命令如何到达 EMS/PCS，取决于产品架构。这里描述职责关系，不指定固定接线或消息必经顺序。[资料 1，第 2 页；资料 2，第 3 页；云平台关系为分析框架]

【G-008｜功率与电量】功率表示能量变化的速率，常用 kW/MW；能量或电量表示一段时间内的数量，常用 kWh/MWh。同一计量边界上，100 kW 恒定运行 1 小时对应 100 kWh；这不自动代表电池侧、并网侧和结算侧电量相同。Ah 是电荷容量，不能直接等同 kWh。[资料 4；资料 3，第 3–5 页]

## 公开资料

2026-10-12 只读核对下列原始资料，内容用中文概括；未上传企业源码。页码指 PDF 内印刷页码。

1. [Sandia / DOE Energy Storage Handbook，第 15 章：Energy Storage Management Systems](https://www.sandia.gov/app/uploads/sites/163/2023/01/ESHB2020_Ch15_ESMS_Nguyen.pdf)。支持 BMS、EMS、SOC/SOH 和典型管理层级；不作为本公司云平台架构的证据。
2. [Sandia / DOE Energy Storage Handbook，第 13 章：Power Conversion Systems](https://www.sandia.gov/app/uploads/sites/163/2021/09/ESHB_Ch13_PCS_Mueller.pdf)。支持 PCS 的电能转换和功率流控制职责。
3. [Sandia / DOE Energy Storage Handbook，第 16 章：Energy Storage Performance Testing](https://www.sandia.gov/app/uploads/sites/163/2021/09/ESHB_Ch16_Testing_Rosewater.pdf)。支持电荷容量、能量及测试工况区别。
4. [EIA：Measuring electricity](https://www.eia.gov/energyexplained/electricity/measuring-electricity.php)。支持功率与能量单位区别。

## 维护方式

修改以上概念后，维护者应核对资料与措辞，重新生成同一 schema 的知识包，更新基础概念文档证据的行号和文件 SHA-256，并发布新的 bundle_id。新任务读取新版本；已有任务输入保留原版本。无需 Agent 在使用时重新读取这些资料。
