# 配置文件编辑器设计

状态：已实现（2026-11-23，结构化表单版）
日期：2026-11-19
范围：全局配置文件 `~/.NoteManager/config.json` 的 Web 编辑器

## 1. 背景

NoteManager 的全局配置目前集中在：

```
~/.NoteManager/config.json
```

它包含监控目录、默认工程、端口、传输方式、socket 路径、外部编辑器等字段。
现在这些内容只能手工编辑 JSON，字段一多容易写错；同时 `config.json` 中有一部分字段
目前并不是启动时的权威配置，直接做“设置页面”容易让用户以为改了就会生效。

本功能只做一件事：**在 Web UI 中编辑这个配置文件本身**。

它不负责“立即应用配置”。页面以按类型分组的表单为主，原始 JSON 作为高级入口；
保存只写文件，配置在 daemon 下次启动时读取。重启时显式传入的 CLI 参数优先于文件值。

## 2. 目标

1. 在 Web UI 中用按类型分组的表单查看、编辑、保存 `~/.NoteManager/config.json`。
2. 保存后明确提示“需要重启 daemon 后生效”。
3. 让 `config.json` 中已支持的字段真正成为 daemon 启动配置源。
4. 重启时 CLI 参数覆盖配置值，且不写回配置文件。
5. 保存过程安全：JSON 校验、已知字段校验、原子写入、自动备份、并发保护。
6. 原始 JSON 作为高级入口保留，用于未知字段和修复无效配置。

## 3. 非目标

- 不实现“保存后立即生效”的动态配置。
- 不实现 Web 端自动重启 daemon。
- 不做项目级配置（`.nmrui.json`、`.nmrignore`）的编辑器；这些以后另行设计。
- 表单不追求覆盖未来所有未知字段；未知字段通过原始 JSON 维护。
- 不替代现有的“添加目录 / 移除目录 / 默认工程”操作。
- 不允许远程访问；仍只监听本机。

## 4. 术语

- **文件配置**：`config.json` 中的内容，daemon 启动时读取。
- **运行时配置**：daemon 进程实际使用的端口、传输方式、socket 路径等，记录在
  `~/.NoteManager/daemon.json` 中。
- **重启生效**：保存文件后，执行 `nmr start --restart`（或先 stop 再 start）才应用。
- **CLI 覆盖**：重启时显式传入的 `--port` / `--web-port` 只对本次运行生效，不写回文件。

## 5. 当前配置盘点

| 字段 | 位置 | 当前行为 | 本功能后的行为 |
|---|---|---|---|
| `directories` | `config.json` | 启动时恢复监控；add/remove 实时更新 | 启动时读取；文件编辑需重启；继续支持现有 add/remove |
| `default_project` | `config.json` | 控制台「默认」按钮实时更新 | 启动时读取；文件编辑需重启；控制台按钮仍实时 |
| `editor` | `config.json` | 当前每次打开外部编辑器时重新读取 | 改为 daemon 启动时快照，文件编辑需重启 |
| `port` | `config.json` | 当前不参与启动，CLI/默认值决定 | 参与启动：CLI > 配置 > 默认 |
| `web_port` | 目前不在 config | 运行时由 CLI/默认值决定 | 新增到 config；参与启动：CLI > 配置 > 默认 |
| `transport` | `config.json` + 平台 | 当前由 `use_unix_socket()` 决定 | 参与启动；平台约束下可配置 |
| `socket_path` | `config.json` + 平台 | 当前固定默认值 | `transport=unix` 时参与启动，可配置 |
| `daemon.json` | `~/.NoteManager` | 运行时状态 | 仍为运行时状态，不通过本编辑器修改 |
| `web_state.json` | `~/.NoteManager` | 主页面等运行状态 | 不通过本编辑器修改 |
| `.nmrui.json` | 各工程根目录 | 构建时读取主题/插件 | 本功能不处理，后续另做 |
| `.nmrignore` | 各工程根目录 | 扫描时读取 | 本功能不处理，后续另做 |

关键点：当前 `port`、`web_port`、`transport`、`socket_path` 并不是启动权威配置；
本功能必须先把它们接入启动流程，否则编辑器只是“写了一个没人读的文件”。

## 6. 核心设计

### 6.1 文件配置与运行时配置分离

daemon 启动时：

1. 读取 `config.json` 到 `self.config`（文件配置快照）。
2. 解析出本次运行的端口、transport、socket 路径到独立的运行时属性。
3. CLI 显式参数只覆盖运行时属性，不修改 `self.config`，也不写回文件。
4. `daemon.json` 记录实际运行时值。

这样：

- 配置编辑器写文件；
- CLI 参数覆盖本次运行；
- 两者互不污染；
- 下一次重启以文件为准。

### 6.2 启动优先级

#### `nmr start`

```
TCP 端口:    --port > config.port > 内置默认值
Web 端口:    --web-port > config.web_port > 内置默认值
transport:   config.transport > 平台默认值（POSIX unix / Windows tcp）
socket_path: config.socket_path > 默认路径（仅 transport=unix）
```

#### `nmr start --restart`

```
TCP 端口:    --port > config.port > 当前运行值 > 内置默认值
Web 端口:    --web-port > config.web_port > 当前运行值 > 内置默认值
transport:   config.transport > 当前运行值 > 平台默认值
socket_path: config.socket_path > 当前运行值 > 默认路径（仅 transport=unix）
```

#### 自动拉起 daemon（`nmr view/search/add` 等）

```
显式传入的端口参数 > config.port/web_port > 内置默认值
transport/socket 按 config > 平台默认
```

说明：

- CLI 参数不写回 config。
- `nmr start` 发现 daemon 已在运行时仍只提示；要应用新配置必须 `--restart`。
- 旧版本遗留的 `daemon.json` 不参与配置解析，只用于找到正在运行的 daemon。

### 6.3 配置字段语义

#### `directories`

- 字符串数组，绝对路径优先。
- daemon 启动时按数组顺序恢复监控。
- 不存在的目录在启动时跳过并从内存配置移除，同时记录日志（保持现有行为）。
- 控制台新增/移除目录仍走现有 API，立即生效并更新文件。
- 在配置文件编辑器中修改数组：下次重启生效，不会启动/停止当前 watcher。

#### `default_project`

- 字符串或省略。
- 启动时读取，用于 CLI 默认工程。
- 控制台「默认」按钮继续实时修改。
- 配置文件中的修改：下次重启生效。

#### `editor`

- 字符串或字符串数组；支持 `{file}`、`{dir}` 占位符。
- 为兑现“配置需要重启才生效”，改为 daemon 启动时读取并缓存；
  不再每次点击外部编辑按钮时重新读文件。
- 外部编辑器按钮的行为不变，只是配置值来自启动快照。

#### `port`

- TCP 端口，整数 1–65535。
- 当 `transport=tcp` 时使用；当 `transport=unix` 时仍保留用于兼容和 TCP fallback。
- CLI `--port` 可覆盖本次运行。

#### `web_port`

- Web UI HTTP 端口，整数 1–65535。
- 新增字段；旧配置没有时使用当前运行值/内置默认值。
- 修改后必须重启；如果端口变化，旧页面会失联，需要访问新地址。

#### `transport`

- `"unix"` 或 `"tcp"`。
- POSIX 默认 `unix`；Windows 只允许 `tcp`。
- 配置非法时，启动阶段记录警告并回退到平台默认值，避免 daemon 完全起不来。

#### `socket_path`

- 仅 `transport=unix` 时使用；必须是绝对路径。
- 默认 `~/.NoteManager/daemon.sock`。
- 修改后需要重启；CLI 通过 `daemon.json` 里的实际 socket 路径连接。
- `_remove_daemon_info()` 必须清理实际 socket，而不是固定默认路径。

### 6.4 未知字段

- 保留并写回，不因为编辑器不认识就删除。
- 启动时忽略未知字段。
- 这样以后新增配置项不会被旧编辑器破坏。

### 6.5 未重启修改检测

页面重新打开后不能简单地把“这次没有编辑”当成“配置已生效”。daemon 需要持续比较
`config.json` 与当前运行实例：

- `port` / `web_port`：文件值（缺省时取默认值）与运行时端口比较；
- `transport` / `socket_path`：按文件解析出的有效值与运行时属性比较；
- `directories`：文件列表与当前 watcher 集合比较；
- `default_project` / `editor`：文件值与本进程启动/实时应用的值比较；
- 未知字段和纯格式变化不计入未重启修改。

若存在差异，返回 `pending_restart` / `pending_fields`，配置页面和控制台显示持久提示：

```
配置文件与当前运行实例不一致：TCP 端口、Web 端口。
运行实例尚未重启，这些修改不会生效。执行 nmr start --restart 后生效。
```

配置页面在浏览器 back/forward cache 恢复时也会重新请求配置，避免“页面显示已保存、
但运行实例仍是旧值”的假象。

## 7. Web API

### 7.1 `GET /api/config_file`

返回编辑器所需内容：

```json
{
  "ok": true,
  "path": "/home/u/.NoteManager/config.json",
  "exists": true,
  "text": "{ ... }",
  "sha256": "...",
  "mtime_ns": 1790000000000000000,
  "runtime": {
    "pid": 1234,
    "port": 19832,
    "web_port": 19833,
    "transport": "unix",
    "socket_path": "/home/u/.NoteManager/daemon.sock"
  },
  "pending_restart": true,
  "pending_fields": ["port", "web_port"],
  "pending_error": null
}
```

可选参数：

- `?source=backup`：返回 `config.json.bak` 的内容，用于“加载上一版”。

文件不存在时返回 `exists=false`，`text` 可为空或默认模板。

### 7.2 `POST /api/config_file`

请求：

```json
{
  "text": "{ ... }",
  "base_sha256": "读取时的 sha256",
  "force": false
}
```

行为：

1. 校验 `text` 是合法 JSON 对象。
2. 对已知字段做类型/范围校验；未知字段允许。
3. 如果 `base_sha256` 与当前文件不一致，返回 `409`，提示“文件已被外部修改”，
   除非 `force=true`。
4. 备份当前文件到 `config.json.bak`。
5. 原子写入新内容。
6. 返回保存结果和重启提示。

响应：

```json
{
  "ok": true,
  "changed": true,
  "path": "/home/u/.NoteManager/config.json",
  "sha256": "新内容的 sha256",
  "backup_path": "/home/u/.NoteManager/config.json.bak",
  "restart_required": true,
  "pending_restart": true,
  "pending_fields": ["port", "web_port"],
  "runtime": { "...": "..." },
  "saved": {
    "port": 19999,
    "web_port": 19999,
    "transport": "unix"
  },
  "warnings": [
    "web_port 已从 19833 改为 19999；重启后请访问 http://localhost:19999/"
  ]
}
```

错误：

- `400`：JSON 语法错误、顶层不是对象、已知字段类型/范围错误。
- `409`：`base_sha256` 不匹配。
- `403`：跨源请求。
- `500`：IO 失败。

不会执行重启，也不会修改运行时配置。

## 8. 页面设计

### 8.1 路由与入口

- 路由：`GET /config`
- 页面标题：**配置文件编辑器**
- 控制台标题旁或卡片中加入「配置文件」链接。

### 8.2 布局

```
配置文件编辑器
~/.NoteManager/config.json

[当前运行]  PID 1234 | TCP 19832 | Web 19833 | unix
            socket: /home/u/.NoteManager/daemon.sock

⚠ 保存后不会立即生效。执行 nmr start --restart 重启后生效。
   重启时显式传入的 --port / --web-port 会覆盖文件中的值。

[若有未重启修改] 配置文件与当前运行实例不一致：TCP 端口、Web 端口。
                 运行实例尚未重启，这些修改不会生效。

┌ 工程 ───────────────────────────────────────┐
│ 监控目录                                    │
│  [/path/to/notes                    ] [移除] │
│  [/another/project                  ] [移除] │
│  [添加目录]                                  │
│ 默认工程  [下拉：/path/to/notes       ▼]     │
└─────────────────────────────────────────────┘

┌ 网络  [修改后需要重启] ──────────────────────┐
│ TCP 端口   [19832]   Web 端口 [19833]        │
│ 传输方式   [默认/unix/tcp ▼]                 │
│ Socket 路径[/home/u/.../daemon.sock]         │
└─────────────────────────────────────────────┘

┌ 外部编辑器 ─────────────────────────────────┐
│ 模式     [默认（gvim/MacVim） ▼]             │
│ 命令     [gvim {file}]（仅自定义模式显示）   │
└─────────────────────────────────────────────┘

[保存] [重新加载]                    有未保存修改
状态：JSON 有效

▸ 高级：原始 JSON
  [保存原始 JSON] [重新加载] [格式化] [加载上一版]
  ┌──────────────────────────────────────────┐
  │ { ... }                                  │
  └──────────────────────────────────────────┘
```

- 表单字段是主要编辑方式：目录列表、默认工程、端口、传输方式、
  socket 路径、外部编辑器。
- `directories` 用可增删的输入行编辑；`default_project` 从目录列表中选择。
- `editor` 提供“默认（gvim/MacVim）”和“自定义命令”两种模式。
- 表单保存时保留配置中表单未覆盖的未知字段。
- 高级区保留原始 JSON 编辑，用于未知字段、修复无效 JSON 或直接控制完整文件。
- 页面加载时记录 `sha256`，保存时带上；文件被外部修改时提示重新加载。
- 页面加载/保存后根据服务端 `pending_*` 字段持续显示“配置文件与运行实例不一致”的提示。
- 浏览器 back/forward cache 恢复页面时重新拉取配置，避免显示假象。
- 表单或原始 JSON 有未保存修改时，离开页面给出确认。
- 保存成功后显示：
  - 已保存，需要重启；
  - 如果 `web_port` 变化，显示新地址；
  - 可复制的 `nmr start --restart` 命令。

### 8.3 不做的事情

- 不做“保存并重启”按钮。
- 不做保存后自动刷新或热加载。
- 不做重启进度展示。
- 不做项目级 `.nmrui.json` 的表单。

## 9. 校验规则

### 9.1 硬校验（不通过则不写文件）

- 文本必须是合法 JSON。
- 顶层必须是对象。
- `port`、`web_port`：整数（非 bool），1–65535，且两者不相等。
- `transport`：`unix` 或 `tcp`；Windows 上不允许 `unix`。
- `socket_path`：字符串；`transport=unix` 时必须为绝对路径。
- `directories`：字符串数组。
- `default_project`：字符串。
- `editor`：字符串、字符串数组或省略。
- 已知字段类型错误时拒绝保存。

### 9.2 软校验（警告但允许保存）

- 目录不存在。
- `default_project` 不在 `directories` 中。
- 端口当前被占用（当前 daemon 自己占用不算）。
- `editor` 命令找不到。
- `transport`/`socket_path` 与当前平台可能不匹配。
- 其他可能影响启动但无法在保存时确定的问题。

软校验结果在页面显示，用户仍可保存。

## 10. 文件安全与并发

### 10.1 原子写入

- 在配置文件同目录创建临时文件。
- 写入、`flush`、`fsync`。
- 保留原文件权限（至少 `0600`/原 mode）。
- `os.replace` 覆盖。
- 失败时保留原文件，不产生半写文件。

### 10.2 备份

- 每次成功保存前，把当前 `config.json` 复制为 `config.json.bak`。
- 只保留一份上一版。
- 页面「加载上一版」读取该文件；不自动恢复。
- 备份写入失败时是否继续保存？建议：备份失败则中止保存，避免无回退。

### 10.3 并发

- 新增配置写锁，覆盖：
  - 配置文件编辑器保存；
  - add/remove/default 等现有写配置操作。
- 现有运行时操作改为“重新读取当前文件 → 只修改自己负责的字段 → 原子写回”，
  避免覆盖配置编辑器刚保存的内容。
- 配置编辑器保存带 `base_sha256`，检测外部修改。
- 不要求跨进程锁；同一用户通常只有一个 daemon。

## 11. 安全

- Web 服务仍只绑定 `127.0.0.1`。
- `POST /api/config_file` 复用现有同源校验。
- 不新增任意命令执行接口；配置文件中的 `editor` 只是被写入，保存不会执行。
- 配置编辑器是本地高权限功能；不建议把 Web UI 暴露到局域网。
- 如果未来要做远程访问，必须先加认证/CSRF 防护。
- 配置文件编辑器不接受任意路径参数，只操作固定的 `~/.NoteManager/config.json`。

## 12. 兼容性与迁移

- 旧配置没有 `web_port`：使用当前运行值或内置默认值。
- 旧配置的 `port`/`transport`/`socket_path` 过去不参与启动，现在会参与；
  如果值不合法，启动时记录警告并回退到安全默认值，避免 daemon 起不来。
- CLI 显式参数不再写回配置文件。
- `editor` 从“每次读取”改为“启动快照”，这是行为变化，需要写入发布说明。
- `nmr start --restart` 不再无条件继承当前端口，而是按
  `CLI > config > 当前运行值 > 默认值` 解析。
- `daemon.json` 格式保持兼容，只增加必要字段。

## 13. 测试计划

### 单元测试

- 启动优先级：
  - `start` 无参数使用 config；
  - `start --port` 覆盖 config；
  - `start --restart` 使用 config 而非当前运行值；
  - `start --restart --web-port` 覆盖 config；
  - 自动拉起 daemon 使用 config。
- 运行时/文件配置分离：
  - CLI 覆盖不写入 config；
  - add/remove/default 不覆盖配置文件编辑器保存的内容。
- `transport`/`socket_path` 启动、`daemon.json` 记录、socket 清理。
- 配置 API：
  - GET 正常/文件不存在/备份；
  - POST 合法保存、非法 JSON、错误字段类型、端口越界；
  - `base_sha256` 冲突返回 409；
  - 原子写、备份生成、权限保留；
  - 跨源 POST 被拒绝。
- UI：
  - 页面包含编辑器、警告、按钮；
  - JS 包含 `base_sha256`、`nmr start --restart`、Web 端口变化提示。
- 行为变化：
  - `editor` 使用启动快照，修改配置文件后点击外部编辑仍用旧值，重启后更新。
- 未重启检测：
  - 文件与运行实例端口/传输/socket/目录/default/editor 不一致时返回 pending；
  - 配置页与控制台显示持久提示；
  - 重启后 pending 消失；
  - add/remove/default 实时更新的字段不产生误报。

### 手工验证

- 修改端口保存 → 页面提示需重启 → `nmr start --restart` → `nmr status` 显示新端口。
- 修改 Web 端口 → 重启后旧地址失效、新地址可访问。
- `nmr start --restart --web-port` 覆盖配置文件值，且不写回。
- 保存非法 JSON 被拒绝，原文件不变。
- 备份恢复可用。
- 配置编辑器保存后，通过控制台添加/移除工程，编辑器内容不被覆盖。

## 14. 实现步骤（评审通过后）

1. 配置模型与启动优先级：
   - 增加 `web_port`；
   - 文件配置/运行时配置分离；
   - 改造 `cmd_start`、`_ensure_daemon`、`_restart_daemon`；
   - transport/socket 接入启动和清理。
2. 配置文件读写基础设施：
   - 原子写、备份、锁、merge-on-write；
   - 已知字段校验。
3. Web API：
   - `GET /api/config_file`、`POST /api/config_file`；
   - 同源校验、409 冲突。
4. 页面：
   - `/config` 页面、控制台入口；
   - 编辑器、格式化、重新加载、加载上一版、保存提示。
5. 测试与文档：
   - 单元测试、手工验证；
   - 更新 README/HISTORY。

## 15. 已确认的决定

1. `editor` 改为 daemon 启动时快照，配置文件修改后需要重启。
2. `transport` / `socket_path` 本轮一起接入启动流程。
3. 备份采用单份 `config.json.bak` + 「加载上一版」。
4. 端口占用、目录不存在、编辑器命令不存在只警告，不阻止保存。
5. 页面路由 `/config`，控制台入口叫「配置文件」。
6. 本功能只编辑全局 `config.json`，项目级 `.nmrui.json` 以后另做。
7. 页面以按类型分组的表单为主；原始 JSON 保留在「高级」中，用于未知字段和修复。

## 16. 验收标准

- [x] `/config` 以表单形式编辑工程、端口、传输方式、socket 和编辑器。
- [x] 表单保存保留未知字段；高级区可编辑完整原始 JSON。
- [x] 配置修改后返回/重新打开页面仍提示“与当前运行实例不一致”。
- [x] 重启后提示消失；add/remove/default 实时更新不误报。
- [x] 非法 JSON / 顶层非对象 / 已知字段类型错误无法保存。
- [x] 保存成功生成备份，原文件原子替换。
- [x] 保存后不改变当前运行状态，只提示重启。
- [x] `nmr start --restart` 能应用配置中的端口/Web 端口/transport/socket。
- [x] 重启时显式 CLI 参数覆盖配置，且不写回文件。
- [x] Web 端口变化时页面提示新地址。
- [x] 控制台 add/remove/default 不覆盖配置编辑器保存的内容。
- [x] 文档、测试、发布说明更新。
