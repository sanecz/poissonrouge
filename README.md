# PoissonRouge  🐟

A terminal UI to explore and interact with Redfish management API. Browse, inspect json, discover and send GET/POST/PATCH/PUT/DEL requests to manage servers.


Built with [Textual](https://textual.textualize.io/) <3

## Features
- Tree browser: walk the redfish ressource graph
- Action discovery: Actions are added as tree entities, with parameters resolved from bmc json schema when available
- Request editor: inline editor to compose and edit requets with a json body
- Clickable links: uses @odata.id references to navigate easier inside any node in the tree
- Cute fish aquarium: because it's cute

## Usage
Requires at least python3.10

```bash
pip3 install -r requirements.txt
python3 poissonrouge.py
```

Enter your BMC url, username and password.

## Shortcuts
- alt-l: logout of the current BMC
- alt-w: copy json
- F2: toggle editor