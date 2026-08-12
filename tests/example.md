# ASCII Diagram Demo

```text
Replica 侧                         Master 侧
┌─────────┐    请求/响应/数据     ┌─────────┐
│ Replica │ ───────────────► │ Master  │
└─────────┘    响应/RDB/stream └─────────┘
```

```text
Client                     Server
┌───────┐   HTTP/1.1   ┌───────┐
│ App   │ ───────────► │ API   │
│       │ ◄─────────── │       │
└───────┘   JSON       └───────┘
```

```text
   Producer               Consumer
┌──────────┐   items   ┌──────────┐
│ Queue    │ ───────► │ Worker   │
│          │ ◄─────── │          │
└──────────┘          └──────────┘
```

```text
  User                   DB
┌──────┐  query  ┌─────────┐
│ Web  │ ───────► │ SQLite  │
│      │ ◄────── │         │
└──────┘  result └─────────┘
```
