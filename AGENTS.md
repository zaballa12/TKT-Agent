# AgenteTicket Project Conventions

## Architecture
- Keep runtime configuration explicit. Do not reintroduce side-effect imports such as bootstrap modules.
- Centralize environment loading in a dedicated settings module.
- Keep MCP as a context acquisition tool, not as a place for business logic.
- Prefer small functions with one responsibility and avoid repeated state updates inline.
- Keep orchestration functions thin: when a method needs a long de/para, payload normalization, or output shaping, extract that work into a dedicated helper with a clear name.

## AI Configuration
- Do not hard code ticket categories, search heuristics, or business keywords in Python modules.
- Keep agent instructions, category options, action options, and loop policy in editable files under project configuration.
- When new AI behavior is needed, prefer updating configuration files first and code second.
- Prepare configuration loaders so file-backed config can later be replaced by database-backed config with minimal orchestration changes.

## Code Style
- Avoid fallback logic that invents domain behavior when model-driven or config-driven behavior is expected.
- Remove dead code paths instead of leaving alternative implementations embedded in large modules.
- Keep domain payloads focused on business output; observability and diagnostics should live in logging or dedicated tooling.
- Prefer explicit transformation helpers over inline mapping blocks in core flows, especially when the same shape can be reused or tested in isolation.

## MCP and Repository Context
- Filter noisy generated artifacts before loading repository files when possible.
- Accumulate context across rounds rather than repeatedly resetting the repository view.
- Stop iterative analysis when confidence policy is met or no new useful context can be loaded.
