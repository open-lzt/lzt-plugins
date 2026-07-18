<p align="right"><b>English</b> · <a href="README.md">Русский</a></p>

# lzt-plugins

The owner's trusted catalog of **`.py` plugins** for the flow engine ([auto-lzt](https://github.com/open-lzt/auto-lzt)) —
distinct from **FLOW modules** ([docs](https://github.com/open-lzt/auto-lzt/blob/main/docs/modules.md)), which are
graph data (community, CI-validated). The line is the trust boundary:

| | What | Trust |
|---|---|---|
| **Flow modules** | `flow.json` graphs ([docs](https://github.com/open-lzt/auto-lzt/blob/main/docs/modules.md)) | community, CI-checked, installed over the API |
| **Plugins** | executable `.py` (nodes + routers + handlers), this repo | **owner-only**, no sandbox, installed from the bot |

A plugin runs in-process with the account's tokens and money. There is no sandbox by design — which is why this
catalog is the owner's own, and a plugin is installed by the owner from the bot's `/plugins` menu, never over the
public API.

## How the bot installs from here

The flow bot reads `plugins.json` at `LZT_FLOW_PLUGIN_INDEX_URL` and downloads each plugin's `source_url` (a zip)
into `.system/plugins/<name>/`. On the next restart the runtime loads it. Point the engine at this catalog:

```
LZT_FLOW_PLUGIN_INDEX_URL=https://raw.githubusercontent.com/open-lzt/lzt-plugins/main/plugins.json
# For a PRIVATE catalog, also set a token with `repo` read scope:
LZT_FLOW_PLUGIN_INDEX_TOKEN=<github PAT>
```

## Catalog

| Plugin | Nodes | Notes |
|---|---|---|
| `steam-autobuy` | `steam.search`, `steam.fast_buy` | Search Steam accounts with filters + a per-search budget, then buy the best candidate. `dry_run` on by default. The matching `steam-autobuy` FLOW module arranges these nodes. |

## Adding a plugin

1. `plugins/<name>/plugin.py` — a module exposing `PRE_INIT` / `POST_INIT` / `SHUTDOWN` (see
   [auto-lzt docs/plugins.md](https://github.com/open-lzt/auto-lzt/blob/main/docs/plugins.md)),
   plus a `manifest.json`.
2. Zip the plugin files (plugin.py at the archive root) into `dist/<name>.zip`.
3. Add an entry to `plugins.json` (`name`, `version`, `source_url`, `requirements`).

`requirements` are pip-installed once at install time (empty when the plugin uses only what the flow engine
already ships).
