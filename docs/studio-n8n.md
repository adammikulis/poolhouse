# Import n8n JSON into Studio

Choose **Import n8n / Paste JSON** in the Studio node library. Paste an exported workflow object or choose its JSON file, then preview the translation. **Import draft into canvas** adds the configuration with one undo step. It does not save, execute, start a subscription, install dependencies, or contact an imported endpoint. Save the draft when ready.

The preview identifies each translated or blocked node. Blocked nodes retain their labels, positions and indexed connections so they can be replaced and reconnected. They prevent execution, including running another selected branch. Code, expressions, unsupported versions and execution options never become executable Poolhouse configuration.

Current mappings:

| n8n node | Studio result |
| --- | --- |
| Manual trigger, version 1, empty parameters and default execution options | Record containing `{}` on an explicit Run; no live trigger |
| Ollama Chat Model, version 1, literal model selection and empty options | Unbound model; choose a currently installed Poolhouse model |
| Other nodes or unsupported options | Blocked placeholder with replacement guidance |

Credentials, provider model selections, URLs, authentication headers, pinned data, runtime state and history are omitted. Imported identity, approvals and execution-device fields grant nothing. Resources use the normal Poolhouse binding controls. A draft containing unsupported nodes cannot be exported as an executable Poolhouse package; replace those nodes first.

The parser accepts a single workflow object, with named nodes and n8n indexed `connections`; it rejects duplicate or reserved JSON keys, ambiguous names, dangling connections, loops, nonfinite values and oversized or deeply nested content. It does not accept executable JavaScript, URL imports or n8n package archives.

Format references: [official export documentation](https://docs.n8n.io/workflows/export-import/), [official exported example](https://github.com/n8n-io/self-hosted-ai-starter-kit/blob/main/n8n/demo-data/workflows/srOnR8PAY3u4RSwb.json), [connection contract](https://github.com/n8n-io/n8n/blob/master/packages/workflow/src/interfaces.ts), and [manual trigger behavior](https://github.com/n8n-io/n8n/blob/master/packages/nodes-base/nodes/ManualTrigger/ManualTrigger.node.ts).
