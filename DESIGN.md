---
"name": "需求 Agent"
"description": "用户指定 GitHub 浅色语言的本机需求工作台"
"colors":
  "bg-default": "#fff"
  "bg-muted": "#f6f8fa"
  "fg-default": "#1f2328"
  "fg-muted": "#59636e"
  "border-default": "#d1d9e0"
  "accent-fg": "#0969da"
  "accent-muted": "#ddf4ff"
  "action-primary": "#1f883d"
  "success-fg": "#1a7f37"
  "success-muted": "#dafbe1"
  "danger-fg": "#d1242f"
  "danger-muted": "#ffebe9"
  "attention-fg": "#7d5700"
  "attention-muted": "#fff8c5"
  "stage-marker": "#fd8c73"
  "neutral-hover": "#eef1f4"
  "neutral-active": "#e6eaf0"
  "send-hover": "#0550ae"
"typography":
  "headline":
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"Segoe UI\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "22px"
    "lineHeight": 1.4
    "letterSpacing": "-0.02em"
  "title":
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"Segoe UI\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "18px"
    "fontWeight": 650
    "lineHeight": 1.5
  "body":
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"Segoe UI\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "14px"
    "fontWeight": 400
    "lineHeight": 1.5
  "reading-body":
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"Segoe UI\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "14px"
    "fontWeight": 400
    "lineHeight": 1.75
  "control":
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"Segoe UI\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "13px"
    "fontWeight": 500
    "lineHeight": "20px"
  "label":
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"Segoe UI\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "12px"
    "lineHeight": 1.5
  "table-header":
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"Segoe UI\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "12px"
    "fontWeight": 600
    "lineHeight": 1.6
"rounded":
  "control": "6px"
  "surface": "8px"
  "dialog": "12px"
"spacing":
  "4": "4px"
  "6": "6px"
  "8": "8px"
  "10": "10px"
  "12": "12px"
  "16": "16px"
  "20": "20px"
  "24": "24px"
  "32": "32px"
"components":
  "button-primary":
    "backgroundColor": "{colors.action-primary}"
    "textColor": "{colors.bg-default}"
    "typography": "{typography.control}"
    "rounded": "{rounded.control}"
    "padding": "6px 12px"
  "button-primary-hover":
    "backgroundColor": "{colors.success-fg}"
  "button-secondary":
    "backgroundColor": "{colors.bg-muted}"
    "textColor": "{colors.fg-default}"
    "typography": "{typography.control}"
    "rounded": "{rounded.control}"
    "padding": "6px 12px"
  "button-send":
    "backgroundColor": "{colors.accent-fg}"
    "textColor": "{colors.bg-default}"
    "rounded": "{rounded.control}"
  "input":
    "backgroundColor": "{colors.bg-default}"
    "textColor": "{colors.fg-default}"
    "rounded": "{rounded.control}"
    "padding": "8px 10px"
  "status-pill":
    "backgroundColor": "{colors.bg-muted}"
    "textColor": "{colors.accent-fg}"
    "typography": "{typography.label}"
    "rounded": "{rounded.control}"
    "padding": "4px 8px"
  "resume-card":
    "backgroundColor": "{colors.bg-muted}"
    "textColor": "{colors.fg-default}"
    "rounded": "{rounded.surface}"
    "padding": "14px 16px"
  "table-header":
    "backgroundColor": "{colors.bg-muted}"
    "textColor": "{colors.fg-muted}"
    "typography": "{typography.table-header}"
    "padding": "10px 12px"
---

# Design System: 需求 Agent

## Overview

**Creative North Star: "GitHub 浅色需求工作台"**

以用户确认的 GitHub 浅色语言承载高密度需求工作：白色内容、浅灰索引与工具条、深灰正文和细边框。蓝色标识链接、选择及发送，绿色标识主要确认动作；视觉层级服务于阅读、核对与下一步。

这是产品界面的系统记录，依据当前构建所用的源文件提取；系统 UI 字体用于操作和正文，没有独立营销展示字体。原型、历史设计和 Word 导出各自保留身份，本规范不改变它们的排版。

聊天外框以纵向 flex 占满工作区的可用空间，外框隐藏溢出，记录区单独滚动。输入区保持正常文档流且不收缩，不能覆盖聊天记录；默认输入为两行，已有历史消息时隐藏介绍，为当前对话保留空间。

**Key Characteristics:**
- 白色工作区与浅灰导航，细边框分区。
- 正文保持可读字号，表格获得主要空间。
- 链接与选择用蓝色，主要确认用绿色。
- 当前阶段通过下划线与文字权重识别。

记录边界：主题由 `web/src/product-theme.css` 统一覆盖，组件结构来自 `main.tsx`、`shell.tsx`、`ConnectedWorkspace.tsx`、`RequirementTable.tsx` 与 `PrewriteBatchTable.tsx`。这是一层主题覆盖，旧样式表仍保留，并非全量 CSS 重构。全局规则描述复用语言，不把某一任务的首屏构图推广为全部页面的固定模板。

## Colors

低彩度中性色承载正文和工具，语义色只标记操作与状态。前文 frontmatter 是颜色值的规范来源。sidecar 的八阶 OKLCH 色带由这些原色派生，仅用于面板预览，不是新增的产品颜色 token。

### Primary

- **确认绿**（action-primary / success-fg）：主要确认按钮及其悬停态。
- **链接蓝**（accent-fg / accent-muted）：链接、选中控件、用户消息底色和发送动作；发送悬停使用 send-hover。

### Secondary

- **成功绿**（success-fg / success-muted）：完成与成功状态。
- **问题红**（danger-fg / danger-muted）：错误正文和缺失提示底色。
- **注意黄**（attention-fg / attention-muted）：准备提示、需要关注的状态。
- **阶段珊瑚**（stage-marker）：当前阶段底部短线，仅作为阶段识别标记。

### Neutral

- **内容白**（bg-default）：主体、输入和表格单元格。
- **工具浅灰**（bg-muted）：侧栏、工具条、表头和恢复卡。
- **正文深灰**（fg-default）：标题与正文。
- **说明灰**（fg-muted）：标签、时间和辅助说明。
- **分隔灰**（border-default）：一像素边界。
- **交互浅灰**（neutral-hover / neutral-active）：中性按钮悬停及按下/选择。

**The Semantic Action Rule.** 蓝色承担导航、选择和发送；绿色承担主要确认。状态必须同时有文字说明。

## Typography

**Display Font:** 没有独立展示字体或营销 display 角色。
**Body Font:** 系统 UI 字体栈，包含中文 Microsoft YaHei 回退。
**Label/Mono Font:** 标签沿用相同 UI 字体，无额外品牌等宽字体。

**Character:** 紧凑、直接、为中文长文与表格编辑服务。字号层级小而明确，依靠权重、颜色和间距辅助扫描。

### Hierarchy

- **Headline**：常规工作区标题，使用 headline；移动端助手标题缩至（20px）。
- **Title**：项目栏标题使用 title；表格标题通常（20px），桌面紧凑规则中可为（18px）；阶段局部标题并不统一强制放大。
- **Body**：界面与表格正文使用 body；说明段落行高（1.65），聊天正文使用 reading-body。
- **Control**：常规按钮使用 control；工具条与定位操作可使用（12px）。
- **Label**：辅助说明和表头使用 label / table-header；项目元信息及键盘提示局部使用（11px），不能扩展为正文。
- 聊天消息最大宽度为（min(92%, 80ch)），聊天容器最大宽度为（1040px），长段落以可读行长组织。

**The Reading Space Rule.** 窄屏与低高度先调整导航、间距和区域结构，保留正文与编辑字段的阅读尺度。

## Layout

桌面使用全高（100dvh）应用壳，左侧项目索引宽（224px），右侧为可收缩的主工作区。常规项目栏高（56px），阶段栏高（44px）；主工作区横向留白（24px）。反复使用的间距为 frontmatter spacing 中的步骤，局部值按组件保留，不把它伪装成唯一数学比例。

- **大屏（min-width: 1800px）**：侧栏（256px），工作区和项目栏横向留白（32px），表格单元格留白（16px）。
- **中等桌面（761px–1150px）**：侧栏（190px），工作区横向留白（16px），表格单元格留白（10px）。
- **移动端（max-width: 760px）**：侧栏隐藏，项目选择进入原生 details 折叠区域；项目栏换行，工作区横向留白（12px），主要文字保留阅读尺度。阶段仍为横向操作行。
- **表格窄屏（max-width: 700px）**：需求表最小宽（620px），成文检查表最小宽（780px），在表格区域横向滚动；对比冲突的两列改为单列。表格定位条可横向滚动。
- **低高度桌面（max-height: 800px、min-width: 761px）**：压缩标题间距，隐藏重复说明。另有（851px）起的旧表格紧凑规则及（740px）高度规则，最终值由选择器优先级共同决定。
- **更短桌面（max-height: 650px、min-width: 761px）**：项目栏（44px）、阶段栏（36px），进一步缩小标题与确认栏的外部间距，编辑文字保持（14px）。
- 助手默认单列；打开资料上下文后，桌面增加（300px）右侧区域并允许该区域独立滚动，移动端在同一工作区显示资料上下文、暂时隐藏聊天区。
- 聊天外框与其父级以纵向 flex 分配可用空间，设置 min-height: 0 和 overflow: hidden；记录区 flex: 1，最小高度（140px），overflow: auto，保留稳定滚动条槽位。消息自身不收缩；记录区纵向留白（10px）、消息间距（16px）。
- 输入区为 position: static、flex: none，处于记录区之后的正常文档流，顶部留白（8px）；输入、发送和后续操作各占自己的空间，不通过 sticky 或 fixed 覆盖历史。表格仍以一个主要纵向滚动区配合不收缩的页内确认栏。
- 聊天外框通过 data-has-history 标记是否已有消息；有历史时隐藏介绍标题和说明，无历史时显示邀请内容。
- 专注核对模式隐藏侧栏、项目栏和阶段栏，为同一张表释放空间；不创建第二套产品页面。
- 普通工作区最多保留两个；未保存、运行中和当前任务面板指向的项目保留挂载，跨项目查看结果仍保留原任务身份与助手优先入口。

## Elevation & Depth

主工作区是平的：白底内容、浅灰分区与细边框构成层次；主题明确清除常规按钮和内容卡的阴影。模态框/来源抽屉及短暂请求反馈使用柔和浮层阴影。旧表格确认栏仍可能继承极浅阴影，此值只是残留实现，不是新增组件的标准。

### Shadow Vocabulary

- **浮层**（`0 12px 40px #1f23282b`）：主题中的模态框和来源抽屉。
- **请求反馈**（`0 8px 28px #1f232826`）：请求处理反馈浮层。

**The Flat Workspace Rule.** 主要内容通过留白、细边框和浅灰底区分；浮层与短暂反馈才使用主题阴影。

## Shapes

轻度圆角配合一像素描边。控件与表格滚动边界使用 control；恢复卡、消息和编辑区使用 surface；浮层使用 dialog。主体工作页与阶段选项保持直角，不把每个分区包装成卡片。品牌标记使用局部（7px）圆角；阶段编号为小圆形，两者不是通用容器半径。

## Components

### Buttons

紧凑、清楚的操作控件。

- **Shape:** control 圆角；常规按钮内边距（6px 12px），最小高度（32px）。
- **Primary:** 确认绿、白字、成功绿边框，悬停变深；查看或编辑本身不因此获得确认色。
- **Secondary:** 工具浅灰底、分隔灰边框、正文深灰，悬停 neutral-hover，按下 neutral-active。
- **Send:** 链接蓝底白字，悬停 send-hover，保留发送这一明确动作身份。
- **Focus / Disabled:** 主题全局为（2px）蓝色 outline、偏移（3px）；禁用透明度（0.55）。按钮状态过渡（120ms ease-out），减弱动态时取消过渡。旧表格局部焦点仍有差异，见最后的未规范化说明。

### Chips

轻量的文字状态，不假装成另一个主要按钮。

- 通用状态 pill 使用浅灰底、蓝字、control 圆角和（4px 8px）内边距。
- 当前阶段编号为（20px）圆形标签；注意状态用注意黄底与文字配合。
- 缺失与完成标签存在旧局部配色；不把全部局部色收录为主题 palette。

### Cards / Containers

按需要围合信息，主内容本身不堆叠卡片。

- 恢复卡使用浅灰底、分隔灰描边、surface 圆角和（14px 16px）内边距。
- 工作页主内容无圆角、描边和阴影；表格滚动区使用 control 圆角及一像素边框。
- 请求反馈和模态框参照 Elevation & Depth。

### Inputs / Fields

白底实线描边，直接可编辑。

- 通用字段使用 control 圆角、（8px 10px）内边距、（14px / 1.6）文字；placeholder 使用说明灰。
- 文本域允许纵向调整高度；聊天输入外框使用 surface 圆角及（10px）内边距，内部 textarea 无边框、内边距（4px），最小高度（52px）、最大高度（160px）。默认聊天输入为两行（rows: 2），用户主动进入的直接填写模式为六行（rows: 6）。
- 编辑表格 textarea 最小高度（72px），内边距（9px），行高（1.65）。
- 保留可见焦点、语义错误说明与 disabled 状态；不能仅凭颜色传递缺失。

### Navigation

索引与阶段分开表达。

- 侧栏项目项使用（6px）圆角和（10px 9px）内边距；名称（13px / 600），元信息（11px）。选中使用 neutral-active，悬停局部浅灰。
- 阶段选项为无背景、无圆角的横向行，当前阶段加珊瑚底线及（600）字重。移动端等分可用空间。
- 项目图标为（16px）线条 SVG；普通界面图标为（18px），strokeWidth（1.7），不依赖图标字体。

### Review Tables and Conflict Comparison

表格承担正文阅读、编辑与统一确认。

- 表头浅灰底、说明灰字、（12px / 600），顶部 sticky；单元格白底、（14px / 1.6），常规留白（12px）。
- 确认操作位于表格滚动区之外；缺失项定位、分组与需求跳转服务于同一张表。
- 冲突比较保留“我的修改 / AI 新建议”并列阅读，窄屏转为纵向；逐项选择后统一保存。现有暖色外框与浅绿比较块是局部遗留样式，不作为通用新卡片颜色。

## Do's and Don'ts

### Do:

- **Do** 使用主题的语义颜色，并将状态文字与状态色一起显示。
- **Do** 保留清晰键盘焦点、原生滚动条和减弱动态偏好。
- **Do** 为长表格保留独立滚动区、可读列宽与可见的确认操作。
- **Do** 以实际产品入口和当前实现校验新页面；原型与 Word 导出按各自规范处理。

### Don't:

- **Don't** 用整页缩放压缩小屏文字；当前工作区明确使用 zoom: 1。
- **Don't** 把绿色用于全部导航或把蓝色链接当成统一确认。
- **Don't** 继承旧 CSS 中残留的绿色焦点、暖色告警边框或局部阴影作为新主题标准。
- **Don't** 为工作页新增装饰性大标题、眉题、渐变或无关插图；当前主题隐藏旧眉题。

未规范化、未修复：旧样式仍有绿色表格焦点、暖色缺失/冲突边框、局部浅绿底及确认栏阴影；主题覆盖没有重构它们。源中的品牌“需”是保留的产品文字标记，不是推荐 glyph icon 系统；旧“＋ 新建”字形装饰也不作为未来图标规范。本次只记录已完成界面，未修改实现。验收截图与审查结果由本轮独立审查提供，本文件不是新增浏览器测试或业务验收结论。

