<p align="right"><a href="README.en.md">English</a> · <b>Русский</b></p>

# lzt-plugins

Доверенный каталог **`.py`-плагинов** владельца для flow-движка ([auto-lzt](https://github.com/open-lzt/auto-lzt)) —
в отличие от **FLOW-модулей** ([docs](https://github.com/open-lzt/auto-lzt/blob/main/docs/modules.md)), которые
являются данными-графами (community, проверяются CI). Граница здесь — это граница доверия:

| | Что | Доверие |
|---|---|---|
| **FLOW-модули** | `flow.json`-графы ([docs](https://github.com/open-lzt/auto-lzt/blob/main/docs/modules.md)) | community, проверка CI, ставятся через API |
| **Плагины** | исполняемый `.py` (узлы + роутеры + хендлеры), этот репо | **только владелец**, без песочницы, ставятся из бота |

Плагин выполняется в процессе, с токенами и деньгами аккаунта. Песочницы нет by design — поэтому этот каталог
принадлежит самому владельцу, и плагин ставится владельцем из меню бота `/plugins`, никогда не через публичный API.

## Как бот ставит отсюда

Flow-бот читает `plugins.json` по адресу `LZT_FLOW_PLUGIN_INDEX_URL` и скачивает `source_url` каждого плагина
(zip) в `.system/plugins/<name>/`. На следующем рестарте рантайм его загружает. Наведи движок на этот каталог:

```
LZT_FLOW_PLUGIN_INDEX_URL=https://raw.githubusercontent.com/open-lzt/lzt-plugins/main/plugins.json
# Для ПРИВАТНОГО каталога добавь токен с read-доступом к `repo`:
LZT_FLOW_PLUGIN_INDEX_TOKEN=<github PAT>
```

## Каталог

| Плагин | Узлы | Описание |
|---|---|---|
| `steam-autobuy` | `steam.search`, `steam.fast_buy` | Ищет Steam-аккаунты по фильтрам с бюджетом на поиск, затем покупает лучшего кандидата. `dry_run` включён по умолчанию. Соответствующий FLOW-модуль `steam-autobuy` связывает эти узлы. |

## Как добавить плагин

1. `plugins/<name>/plugin.py` — модуль, экспортирующий `PRE_INIT` / `POST_INIT` / `SHUTDOWN` (см.
   [auto-lzt docs/plugins.md](https://github.com/open-lzt/auto-lzt/blob/main/docs/plugins.md)),
   плюс `manifest.json`.
2. Заархивируй файлы плагина (plugin.py в корне архива) в `dist/<name>.zip`.
3. Добавь запись в `plugins.json` (`name`, `version`, `source_url`, `requirements`).

`requirements` ставятся через pip один раз при установке (пусто, если плагину хватает того, что flow-движок
уже поставляет).
