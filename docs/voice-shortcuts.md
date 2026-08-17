# Adding shopping items by voice

The shopping API is designed so an Apple Shortcut, and therefore Siri, can add an item without knowing any database ids.

Create a Shortcut with a single **Get Contents of URL** action:

| Field | Value |
|---|---|
| URL | `http://<your-rally-host>:8000/api/shopping/items` |
| Method | `POST` |
| Request Body | `JSON` |
| `name` (Text) | Ask Each Time, or a Dictated Text variable |
| `store` (Text) | e.g. `Costco`, optional |

Name it something like "Add to shopping list", then say *"Hey Siri, add to shopping list."*

Two details make this hold up in a kitchen:

- **Stores are referenced by name, not id.** A shortcut that hardcodes `store_id: 3` breaks silently the first time that store is deleted and recreated.
- **An unrecognized store name is not an error.** The item lands in the "Anywhere" catch-all instead, because a hard failure mid-dictation is worse than a slightly misfiled item. Unknown names never auto-create a store.

Adding an item already on the open list for that store returns the existing item rather than creating a duplicate, so a repeated "add milk" is harmless.

> **Note:** a HomePod cannot join a Tailscale tailnet, so a kitchen-speaker shortcut needs Rally's LAN address. Shortcuts on a phone can use MagicDNS.
