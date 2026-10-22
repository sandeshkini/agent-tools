# How results show in Sandesh's Chief of Staff panel (cos)

Your replies show in his cos panel. A result with structure is shown, not narrated:

- **Several items, numbers, steps, a comparison, a table or progress → end your reply with one ```cos-ui block**,
  not a Markdown table or numbers in sentences. Load the `cos-ui` skill for the catalog; the syntax is JSX-like tags,
  never YAML or JSON:

  ````
  ```cos-ui
  <Card title="Disk audit: 2 of 5 over 80%" status="warn">
    <Row><Stat value="5" label="disks"/><Stat value="2" label="over 80%" tone="warn"/></Row>
    <Table columns={["Disk", "Used"]} rows={[["/data", "91%"], ["/", "84%"]]}/>
  </Card>
  ```
  ````

  One lean `Card`: a `Row` of `Stat`s, a `Table`, a `Chart`, `Steps`, a `Grid` of `Card`s for options. Richer means
  structured, not busier.
- **A job with several steps → the crew `live_card` tool** (deferred: load it with ToolSearch
  `select:mcp__crew__live_card`). Call it at the start and after each step with the same id and the whole card each
  time (the `ui` is the inside of a cos-ui block, e.g. a `Card` with
  `<Steps items={[{text: "Tests", state: "done"}, {text: "Deploy", state: "now"}]}/>`), instead of a message per step. Its last
  version stays as your update card.
- A `cos-card` is only for one question he has to answer. Finished work with nothing to show: one plain line.
