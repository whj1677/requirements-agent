---
"name": "需求 Agent"
"description": "浅色中性色与蓝紫主操作的本机需求工作台"
"colors":
  "bg-default": "#f5f6fa"
  "bg-muted": "#f0f1f6"
  "bg-inset": "#f8f9fc"
  "paper": "#ffffff"
  "fg-default": "#242635"
  "fg-muted": "#707589"
  "border-default": "#e2e4ed"
  "border-strong": "#cfd3e0"
  "accent-fg": "#5856c9"
  "accent-hover": "#4644ad"
  "accent-muted": "#ededff"
  "action-primary": "#5856c9"
  "focus": "#6462d8"
  "success-fg": "#237a64"
  "success-muted": "#e8f6f0"
  "danger-fg": "#b4434f"
  "danger-muted": "#fff0f1"
  "attention-fg": "#8a611d"
  "attention-muted": "#fff7e7"
"typography":
  "headline":
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"Segoe UI\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "22px"
    "lineHeight": 1.4
  "title":
    "fontSize": "18px"
    "fontWeight": 650
    "lineHeight": 1.5
  "body":
    "fontSize": "14px"
    "fontWeight": 400
    "lineHeight": 1.55
  "control":
    "fontSize": "13px"
    "fontWeight": 550
    "lineHeight": "20px"
  "label":
    "fontSize": "12px"
    "lineHeight": 1.5
"rounded":
  "control": "9px"
  "surface": "14px"
  "dialog": "16px"
"spacing":
  "4": "4px"
  "6": "6px"
  "8": "8px"
  "10": "10px"
  "12": "12px"
  "16": "16px"
  "18": "18px"
  "20": "20px"
  "28": "28px"
"components":
  "button-primary":
    "backgroundColor": "{colors.action-primary}"
    "textColor": "#ffffff"
    "typography": "{typography.control}"
    "rounded": "{rounded.control}"
    "padding": "7px 12px"
  "button-secondary":
    "backgroundColor": "{colors.paper}"
    "textColor": "{colors.fg-default}"
    "typography": "{typography.control}"
    "rounded": "{rounded.control}"
    "padding": "7px 12px"
  "input":
    "backgroundColor": "{colors.paper}"
    "textColor": "{colors.fg-default}"
    "rounded": "{rounded.control}"
    "padding": "8px 10px"
  "table-header":
    "backgroundColor": "#f3f4f8"
    "textColor": "{colors.fg-muted}"
    "typography": "{typography.label}"
    "padding": "10px 12px"
---

# Design System: 需求 Agent

## Overview

**Creative North Star: 安静、现代、任务优先。**

浅色中性色构成工作区，蓝紫色只承担当前主操作、选择和焦点。白色编辑面、细分隔线、轻度圆角和少量柔和阴影建立层级；不使用装饰性渐变、插画或与当前任务无关的大标题。

这是当前产品界面的系统记录，依据 `web/src/product-theme.css` 及实际组件结构整理。原型、历史设计和 Word 导出各自保留身份，本规范不改变它们的排版。

**Key Characteristics:**

- 一个任务状态只有一个清晰主操作。
- 导航、查看、准备与真正调用模型的动作使用不同文案。
- 表格和聊天输入获得主要空间，外围栏在低高度下收紧。
- 正文与编辑文字不随窗口缩小，布局按有效 CSS 视口适配。

## Colors

低彩度中性色承载正文和工具，语义色同时配合文字状态。

### Primary

- **主操作蓝紫**（accent-fg / action-primary）：当前主按钮、选中项与关键链接。
- **主操作悬停**（accent-hover）：主按钮悬停与按下。
- **选中浅紫**（accent-muted）：当前项目、阶段或轻量选择背景。

### Secondary

- **成功绿**（success-fg / success-muted）：完成与成功状态，不替代主操作色。
- **问题红**（danger-fg / danger-muted）：错误正文和缺失提示。
- **注意黄**（attention-fg / attention-muted）：准备提示和需要关注的状态。

### Neutral

- **工作区浅灰**（bg-default）：应用背景。
- **工具浅灰**（bg-muted / bg-inset）：工具条、分区与次级背景。
- **内容白**（paper）：输入、表格单元格与主要内容面。
- **正文深灰**（fg-default）：标题与正文。
- **说明灰**（fg-muted）：标签、时间和辅助说明。
- **分隔灰**（border-default / border-strong）：一像素边界与悬停边界。

## Typography

**Body Font:** 系统 UI 字体栈，包含中文 Microsoft YaHei 回退；没有独立展示字体。

- 常规工作区标题使用 18–22px，局部表格标题在低高度下保持 18px。
- 正文和编辑字段使用 14px；说明文字通常为 12–13px。
- 控件文字为 13px / 20px，依靠字重、间距和颜色建立扫描层级。
- 小屏不能通过整页 zoom 或缩小编辑字号换取空间。

## Layout

桌面使用全高 `100dvh` 应用壳，默认左侧项目索引为 240px，右侧为可收缩主工作区。

- **大屏（min-width: 1800px）**：侧栏 264px，工作区横向留白 36px。
- **中等桌面（max-width: 1350px）**：侧栏 220px，工作区横向留白 20px。
- **缩放与窄桌面（max-width: 1180px）**：固定侧栏改为顶部项目抽屉，不再挤压正文。
- **移动端（max-width: 760px）**：项目栏与工具区换行，工作区横向留白 12px；表格在自己的区域横向滚动。
- **低高度桌面（max-height: 760px）**：压缩项目栏、阶段栏、标题间距和确认栏外距。
- **更短桌面（max-height: 590px）**：进一步压缩外围控件，编辑文字仍保持 14px。
- 宽度至少 1001px 且高度不超过 760px 时，需求表滚动区至少占有效视口高度的一半（边框额外计入）。
- 专注核对隐藏重复项目与阶段区域，为同一张表释放空间，不创建第二套页面。

聊天外框以纵向 flex 分配空间，记录区独立滚动；输入区保持正常文档流，不覆盖历史。需求表使用一个主要滚动区，确认操作位于滚动区之外。定位需求时显式避开 sticky 表头，保证目标输入完整可见。

## Elevation & Depth

主要内容依靠留白、细边框和浅色背景分层。常规卡片与按钮只有极浅阴影，模态框、来源抽屉和请求反馈使用较明显但柔和的浮层阴影。

- **常规面**：`0 1px 2px rgba(31,35,58,.04), 0 4px 14px rgba(31,35,58,.035)`。
- **浮层**：`0 16px 48px rgba(31,35,58,.14)`。

## Shapes

控件使用 9px 圆角，主要内容面使用 12–14px，浮层使用 16px。圆角用于区分可交互控件与内容层，不把每个段落都包装成卡片。

## Components

### Buttons

- **Primary**：蓝紫底、白字，只用于当前任务的主操作；禁用或尚未满足条件时不使用主色诱导点击。
- **Secondary**：白底或浅灰底、分隔灰边框，用于查看、返回、准备和辅助操作。
- **Focus / Disabled**：全局 2px 焦点描边、3px 偏移；禁用状态同时降低透明度并保留可读文案。

### Navigation

- 桌面项目索引与阶段导航分开表达，当前项目使用浅紫底。
- 窄桌面项目选择收进顶部抽屉；选择项目后回到需求助手，不自动整理或调用模型。
- 阶段选项是轻量分段控件，不使用多组重复主按钮。

### Inputs / Fields

白底实线描边，通用字段为 14px / 1.6；文本域允许纵向调整高度。表格 textarea 最小高度 72px，定位后必须完整处于 sticky 表头下方和滚动区底边上方。

### Review Tables

表头浅灰底、说明灰字、12px / 600，顶部 sticky；单元格白底、14px 正文。缺失项定位、分区定位和需求跳转服务于同一张表，专注模式进入与退出不能卸载输入或丢失草稿。

### Document Preparation

- 阻塞清单按钮使用“去准备 MRD／PRD”“去核对”“去更新”，只负责进入对应处理区。
- 文档工具栏只保留当前文档的一个生成主按钮：“核对并生成／更新”。
- 用户点击 AI 生成主按钮后，后台核对当前输入、资料与模型配置；通过后直接开始，不再弹出逐次授权或要求再次点击运行。缺项、版本变化与运行中的任务仍阻止重复提交，并保留输入。
- 两份文档未齐时，底部继续操作为中性禁用提示，不与当前文档生成争夺注意力。

## Do's and Don'ts

### Do

- 使用语义颜色，并将状态文字与状态色一起显示。
- 保留清晰键盘焦点、原生滚动条和减弱动态偏好。
- 为长表格保留独立滚动区、可读列宽与可见确认操作。
- 以实际有效视口验证 Windows 显示缩放场景。

### Don't

- 不用整页缩放压缩小屏文字。
- 不让多个按钮同时表现为当前主操作。
- 不把“进入准备区”写成“生成”，也不让点击查看直接触发模型任务。
- 不为工作页新增装饰性大标题、渐变或无关插图。

## Verification Boundary

界面回归使用隔离 ASGI、合成资料和静态验证构建，不调用真实模型、不写日常数据。有效视口覆盖 1920×1080、1536×864、1366×650、1280×720、1280×640、1093×614、1093×550、1024×576、946×540 和 911×512。此等效视口验证不冒充实体 Windows 150% 显示设置的人工验收。
