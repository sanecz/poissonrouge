#!/usr/bin/env python3

import json
import httpx
import asyncio
import re

from dataclasses import dataclass
from textual import work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.screen import Screen, ModalScreen
from textual.widget import Widget
from textual.widgets import Tree, Static, RichLog, Input, Button, Label, Header, TextArea, Select, TabbedContent, TabPane
from textual.containers import Horizontal, Vertical, Center
from rich.json import JSON
from rich.style import Style
import random

WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


class RedfishError(Exception):
    def __init__(self, status_code: int, message: str, data=None):
        super().__init__(message)
        self.status_code = status_code
        self.data = data


@dataclass
class ActionTarget:
    name: str
    target: str
    params: dict | None = None
    action_info: str | None = None
    schema_ref: tuple | None = None  # (odata_type, raw_action_key), standard actions only

class RedfishClient:
    def __init__(self, base_url, username, password, verify_ssl = False):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.client = httpx.AsyncClient(auth=(username, password), verify=verify_ssl, headers={"Accept": "application/json"}, timeout=30.0)

    async def get(self, path):
        data = await self._get_page(path)

        next_link = data.get("Members@odata.nextLink")
        while next_link:
            page = await self._get_page(next_link)
            data["Members"].extend(page.get("Members", []))
            next_link = page.get("Members@odata.nextLink")
        data.pop("Members@odata.nextLink", None)

        return data

    async def _get_page(self, path):
        if not path.startswith("/"):  path = "/" + path
        resp = await self.client.get(self.base_url + path)
        resp.raise_for_status()
        return resp.json()

    async def get_etag(self, path):
        if not path.startswith("/"): path = "/" + path
        resp = await self.client.get(self.base_url + path)
        return resp.headers.get("ETag")

    async def request(self, method, path, body=None, etag=None):
        if not path.startswith("/"): path = "/" + path
        headers = {"If-Match": etag} if etag else None
        resp = await self.client.request(method, self.base_url + path, json=body, headers=headers)

        try:
            data = resp.json()
        except ValueError:
            data = None

        if resp.is_error:
            raise RedfishError(resp.status_code, self._extract_error(data, resp.text), data)

        return data

    @staticmethod
    def _extract_error(data, fallback_text):
        try:
            info = data["error"]["@Message.ExtendedInfo"]
            return "; ".join(m.get("Message", str(m)) for m in info)
        except (TypeError, KeyError, IndexError):
            return fallback_text

    async def close(self):
        await self.client.aclose()



class Fish:
    FISH_VARIANTS = [
        ("<><", 0.6, "left"),
        ("><>", 0.3, "right"),
        ("<°)))><", 0.05, "left"),
        ("><((('>", 0.03, "right"),
        ("くコ:彡", 0.015, "left"),
        ("¸.·´¯`·.´¯`·.¸¸.·´¯`·.¸><(((º>", 0.005, "right"),
    ]

    def __init__(self, x, y, direction):
        self.x = x
        self.y = y
        self.direction = direction
        self.symbol = self.pick_symbol(direction)

    def pick_symbol(self, direction):
        filtered = [
            (sym, w, dirn)
            for sym, w, dirn in self.FISH_VARIANTS
            if dirn == direction
        ]

        symbols, weights, _ = zip(*filtered)
        choice = random.choices(symbols, weights=weights)[0]
        return choice

    def move(self, width):
        if self.direction == "right":
            self.x += 1
            if self.x > width:
                self.x = -len(self.symbol)
        else:
            self.x -= 1
            if self.x < -len(self.symbol):
                self.x = width


class FishBackground(Widget):
    def __init__(self, num_fish=12):
        super().__init__()
        self.fish = []
        self.num_fish = num_fish

    async def on_mount(self):
        width = self.app.size.width
        height = self.app.size.height
        for _ in range(self.num_fish):
            x = random.randint(0, width)
            y = random.randint(0 + self.app.size.height // 2, height - 1)
            direction = random.choice(["left", "right"])
            self.fish.append(Fish(x, y, direction))

        self.set_interval(0.15, self.animate)

    def animate(self):
        width = self.size.width
        for f in self.fish:
            f.move(width)
        self.refresh()

    def render(self):
        width = self.size.width
        height = self.size.height
        canvas = [[" " for _ in range(width)] for _ in range(height)]

        for f in self.fish:
            for i, c in enumerate(f.symbol):
                x = int(f.x + i)
                y = int(f.y)
                if 0 <= x < width and 0 <= y < height:
                    canvas[y][x] = c

        return "\n".join("".join(row) for row in canvas)


class RedfishLogin(Screen):
    BINDINGS = [("enter", "submit", "Submit")]

    def compose(self) -> ComposeResult:
        with Center() as center:
            center.border_title = "Login"
            yield Label("URL:")
            yield Input(placeholder="https://192.168.1.240", id="base")
            yield Label("Username:")
            yield Input(placeholder="root", id="user")
            yield Label("Password:")
            yield Input(placeholder="calvin", password=True, id="password")
            yield Button("Submit", id="login_btn")
        yield FishBackground()

    async def on_mount(self):
        self.query_one("#base", Input).focus()

    async def on_button_pressed(self, event):
        if event.button.id == "login_btn":
            event.button.set_loading(True)
            self.action_submit()

    @work(exclusive=True)
    async def action_submit(self):
        base = self.query_one("#base", Input).value.strip()
        user = self.query_one("#user", Input).value.strip()
        pwd = self.query_one("#password", Input).value
        login = self.query_one("#login_btn", Button)

        if not base or not user or not pwd:
            self.notify("Please fill base URL, username and password", severity="warning")
            return

        client = RedfishClient(base, user, pwd)

        try:
            root = await client.get("/redfish/v1/Chassis")
        except Exception as e:
            self.notify(f"Login / connection failed: {e}", severity="error")
            await client.close()
            return
        finally:
            login.set_loading(False)

        self.app.push_screen(RedfishBrowser(client))


    async def on_key(self, event):
        if event.key == "enter":
            login = self.query_one("#login_btn", Button)
            login.set_loading(True)
            self.action_submit()

class ConfirmModal(ModalScreen):
    DEFAULT_CSS = """
    ConfirmModal {
        align: center middle;
        background: $background;
    }
    ConfirmModal > Vertical {
        width: 60%;
        height: auto;
        max-height: 80%;
        border: double $error;
        background: $surface;
        padding: 1 2;
    }
    ConfirmModal Static {
        margin-bottom: 1;
    }
    ConfirmModal Horizontal {
        height: auto;
        align-horizontal: right;
    }
    ConfirmModal Button {
        margin-left: 1;
    }
    """

    def __init__(self, method, path, body):
        super().__init__()
        self.method = method
        self.path = path
        self.body = body

    def compose(self) -> ComposeResult:
        preview = json.dumps(self.body, indent=2) if self.body else "(empty)"
        with Vertical() as v:
            v.border_title = "Confirm"
            yield Static(f"[b]{self.method} {self.path}[/b]\n\n{preview}")
            with Horizontal():
                yield Button("Cancel", id="cancel")
                yield Button(f"Confirm {self.method}", id="confirm", variant="error")

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == "confirm")


class MyTextArea(TextArea):
    BINDINGS = [
        Binding("ctrl+y", "yank", "Yank", show=False),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._kill_ring = ""

    async def action_delete_to_end_of_line_or_delete_line(self):
        if not self.selection.is_empty:
            self._kill_ring = self.selected_text
            start, end = sorted((self.selection.start, self.selection.end))
            self.delete(start, end)
            return

        start = self.cursor_location
        line_start = self.get_cursor_line_start_location()
        line_end = self.get_cursor_line_end_location()

        if line_start == line_end or start == line_end:
            end = (start[0] + 1, 0)
        else:
            end = line_end

        self._kill_ring = self.get_text_range(start, end)
        await super().action_delete_to_end_of_line_or_delete_line()

    def action_yank(self):
        if self._kill_ring:
            self.insert(self._kill_ring)


REDFISH_PATH_RE = re.compile(r"/redfish/[^\"\s,}\]]*")


class RedfishAction(Widget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.border_title = "Request"

    def compose(self) -> ComposeResult:
        with TabbedContent():
            with TabPane("Edit", id="edit-tab"):
                with Vertical():
                    with Horizontal():
                        yield Select(((line, line) for line in ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]), id="select" )
                        yield Input(placeholder="https://192.168.1.240/redfish/v1/AccountService/Accounts", id="input")
                        yield Button("Send", id="send-btn")
                    yield MyTextArea.code_editor(id="code-editor", language="json", theme="css")
            with TabPane("Response", id="response-tab"):
                yield TextArea.code_editor(id="response-editor", language="json", theme="css", read_only=True)
                
class RedfishBrowser(Screen):
    BINDINGS = [
        ("f2", "toggle_edit", "Toggle edit section"),
        ("alt+w", "copy_json", "Copy JSON"),
        ("alt+l", "logout", "Logout"),
    ]

    def __init__(self, client: RedfishClient):
        super().__init__()
        self.node_index = {}
        self.action_index = {}
        self.client = client
        self.border_title = self.get_header_text()
        self.current_path = None
        self.current_data = None
        self.prefilled_path = None
        self._schema_cache = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            tree = Tree("Redfish Root", id="redfish-tree")
            tree.border_title = "Tree"
            yield tree
            with Vertical():
                yield RichLog(id="details-pane", auto_scroll=False)
                yield RedfishAction(id="code-area")

    def get_header_text(self) -> str:
        return f"Connected to: {self.client.base_url.replace('https://', '')}"

    async def on_mount(self):
        self.code_area = self.query_one("#code-area", RedfishAction)
        self.code_area.display = False

        self.tree_widget = self.query_one("#redfish-tree", Tree)
        self.details_widget = self.query_one("#details-pane", RichLog)
        self.details_widget.border_title = "Details"

        try:
            root_data = await self.client.get("/redfish/v1")
        except Exception as e:
            self.notify(f"Error fetching Redfish root:\n{e}", severity="error")
            return

        self.tree_widget.root.label = "/redfish/v1"
        self.tree_widget.root.data = "/redfish/v1"

        self.node_index["/redfish/v1"] = self.tree_widget.root

        self._add_action_leaves(self.tree_widget.root, root_data)

        for name, path in self._discover_links(root_data):
            if not name.startswith("Links"):
                child = self.tree_widget.root.add(name, path)
                self.node_index[path] = child

        self.tree_widget.show_root = True
        self.tree_widget.root.expand()

    def _discover_links(self, data, prefix=""):
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict) and "@odata.id" in v:
                    if prefix:
                        label =  v["@odata.id"].rstrip("/").split("/")[-1]
                        yield f"{prefix}{k}/{label}", v["@odata.id"]
                    else:
                        yield f"{prefix}{k}", v["@odata.id"]
                elif isinstance(v, dict):
                    for sub_label, sub_path in self._discover_links(v, prefix=f"{prefix}{k}/"):
                        yield sub_label, sub_path
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict) and "@odata.id" in item:
                            oid = item["@odata.id"]
                            label = oid.rstrip("/").split("/")[-1]
                            if k == "Members":
                                yield label, oid
                            else:
                                yield f"{k}/{label}", oid

    def _discover_actions(self, data):
        if not isinstance(data, dict):
            return
        actions = data.get("Actions")
        if not isinstance(actions, dict):
            return
        odata_type = data.get("@odata.type")
        for action_name, action_def in actions.items():
            if action_name == "Oem":
                if isinstance(action_def, dict):
                    for oem_name, oem_def in action_def.items():
                        if isinstance(oem_def, dict) and "target" in oem_def:
                            yield (self._short_action_name(oem_name), oem_def["target"], self._collect_action_params(oem_def), oem_def.get("@Redfish.ActionInfo"), None)
                continue
            if isinstance(action_def, dict) and "target" in action_def:
                yield (
                    self._short_action_name(action_name),
                    action_def["target"],
                    self._collect_action_params(action_def),
                    action_def.get("@Redfish.ActionInfo"),
                    (odata_type, action_name) if odata_type else None,
                )

    @staticmethod
    def _short_action_name(action_name):
        # e.g. "#OemManager.v1_3_0.OemManager#OemManager.ExportSystemConfiguration"
        # -> "ExportSystemConfiguration"
        return action_name.rsplit("#", 1)[-1]

    def _collect_action_params(self, action_def):
        params = {}
        apply_time_support = action_def.get("@Redfish.OperationApplyTimeSupport")
        if isinstance(apply_time_support, dict):
            supported = apply_time_support.get("SupportedValues")
            if supported:
                params["@Redfish.OperationApplyTime"] = f"<one of: {', '.join(supported)}>"
        for k, v in action_def.items():
            if k == "target" or "@" in k:
                continue
            if isinstance(v, dict):
                nested = self._collect_action_params(v)
                if nested:
                    params[k] = nested
        for k, v in action_def.items():
            if k.endswith("@Redfish.AllowableValues") and isinstance(v, list):
                params[k.split("@", 1)[0]] = f"<one of: {', '.join(v)}>"
        return params

    def _add_action_leaves(self, node, data):
        for action_name, target, params, action_info, schema_ref in self._discover_actions(data):
            action = ActionTarget(action_name, target, params, action_info, schema_ref)
            node.add_leaf(f"» {action_name}", action)
            self.action_index[target] = action

    @staticmethod
    def _params_from_action_info(action_info_data):
        params = {}
        for param in action_info_data.get("Parameters", []):
            name = param.get("Name")
            if not name:
                continue
            allowable = param.get("AllowableValues")
            if allowable:
                params[name] = f"<one of: {', '.join(allowable)}>"
            else:
                required = "required" if param.get("Required") else "optional"
                params[name] = f"<{param.get('DataType', 'value')}, {required}>"
        return params

    @staticmethod
    def _schema_id_from_odata_type(odata_type):
        # "#Bios.v1_1_1.Bios" -> "Bios.v1_1_1"
        if not odata_type or not odata_type.startswith("#"):
            return None
        parts = odata_type[1:].split(".")
        if len(parts) < 2:
            return None
        return f"{parts[0]}.{parts[1]}"

    async def _fetch_local_schema(self, schema_id):
        try:
            schema_file = await self.client.get(f"/redfish/v1/JsonSchemas/{schema_id}")
        except Exception:
            return None
        return await self._fetch_schema_from_locations(schema_file.get("Location", []))

    async def _fetch_schema_from_locations(self, locations):
        for location in locations:
            uri = location.get("Uri")
            if uri:
                try:
                    return await self.client.get(uri)
                except Exception:
                    continue
        return None

    @staticmethod
    def _parse_external_ref(ref):
        if ref.startswith("#/") or "#/definitions/" not in ref:
            return None
        file_part, def_name = ref.split("#/definitions/", 1)
        type_name = file_part.rstrip("/").split("/")[-1].split(".")[0]
        return type_name, def_name

    async def _resolve_schema_by_type_name(self, type_name):
        cache_key = f"typename:{type_name}"
        if cache_key in self._schema_cache:
            return self._schema_cache[cache_key]

        schema = None
        try:
            collection = await self.client.get("/redfish/v1/JsonSchemas")
            for member in collection.get("Members", []):
                member_id = member.get("@odata.id", "").rstrip("/").split("/")[-1]
                if member_id == type_name or member_id.startswith(f"{type_name}."):
                    schema_file = await self.client.get(member["@odata.id"])
                    schema = await self._fetch_schema_from_locations(schema_file.get("Location", []))
                    break
        except Exception:
            schema = None

        self._schema_cache[cache_key] = schema
        return schema

    async def _resolve_ref_enum(self, ref, current_schema):
        if ref.startswith("#/definitions/"):
            def_name = ref.rsplit("/", 1)[-1]
            return current_schema.get("definitions", {}).get(def_name, {}).get("enum")

        parsed = self._parse_external_ref(ref)
        if not parsed:
            return None
        type_name, def_name = parsed
        ref_schema = await self._resolve_schema_by_type_name(type_name)
        if not ref_schema:
            return None
        return ref_schema.get("definitions", {}).get(def_name, {}).get("enum")

    async def _params_from_local_schema(self, odata_type, raw_action_key):
        schema_id = self._schema_id_from_odata_type(odata_type)
        if not schema_id:
            return None

        if schema_id not in self._schema_cache:
            self._schema_cache[schema_id] = await self._fetch_local_schema(schema_id)
        schema = self._schema_cache[schema_id]
        if not schema:
            return None

        try:
            ref = schema["definitions"]["Actions"]["properties"][raw_action_key]["$ref"]
            def_name = ref.rsplit("/", 1)[-1]
            parameters = schema["definitions"][def_name].get("parameters", {})
        except (KeyError, TypeError):
            return None

        params = {}
        for name, spec in parameters.items():
            if not isinstance(spec, dict):
                continue
            enum = spec.get("enum")
            if enum:
                params[name] = f"<one of: {', '.join(enum)}>"
            elif "$ref" in spec:
                ref_enum = await self._resolve_ref_enum(spec["$ref"], schema)
                params[name] = ( f"<one of: {', '.join(ref_enum)}>" if ref_enum else f"<see schema: {spec['$ref']}>")
            else:
                required = "required" if spec.get("requiredParameter") else "optional"
                params[name] = f"<{spec.get('type', 'value')}, {required}>"
        return params

    @work(exclusive=True)
    async def on_tree_node_selected(self, event: Tree.NodeSelected):
        action = event.node.data
        if isinstance(action, ActionTarget):
            await self._activate_action(action)
            return

        path = action
        if not path: return

        self.details_widget.clear()
        self.details_widget.loading = True

        try:
            data = await self.client.get(path)
        except Exception as e:
            self.notify(f"Error fetching {path}:\n{e}", severity="error")
            return

        self.current_path = path
        self.current_data = data

        if self.code_area.display and path != self.prefilled_path:
            self._prefill_edit_panel()

        if event.node.label._text[0].startswith("Links"):
            await self._navigate_to_path(path)
        elif not event.node.children:
            self._add_action_leaves(event.node, data)
            for name, child_path in self._discover_links(data):
                child = event.node.add(name, child_path)
                if not name.startswith("Links"):
                    self.node_index[child_path] = child

        self.details_widget.loading = False
        self.details_widget.write(self._render_json_with_links(data))
        self.details_widget.border_title = path

    async def _navigate_to_path(self, path):
        if path in self.action_index:
            await self._activate_action(self.action_index[path])
            return

        if path not in self.node_index:
            accumulated = "/redfish/v1"
            for part in path.split("/")[3:]:
                self.node_index[accumulated].expand()
                previous = accumulated
                accumulated += "/" + part
                if accumulated not in self.node_index:
                    child = self.node_index[previous].add(part, accumulated)
                    self.node_index[accumulated] = child
        else:
            self.node_index[path].parent.expand()
        await asyncio.sleep(0)
        self.tree_widget.select_node(self.node_index[path])

    async def action_jump_to_path(self, path):
        await self._navigate_to_path(path)

    @staticmethod
    def _render_json_with_links(data):
        text = JSON(json.dumps(data, indent=2)).text
        for span in list(text.spans):
            if span.style != "json.str":
                continue
            value = text.plain[span.start:span.end]
            if value.startswith('"/redfish') and "$metadata" not in value:
                path = value.strip('"')
                text.stylize(
                    Style(underline=True, color="cyan", meta={"@click": f"screen.jump_to_path({path!r})"}),
                    span.start,
                    span.end,
                )
        return text

    def action_copy_json(self):
        if self.current_data is not None:
            self.app.copy_to_clipboard(json.dumps(self.current_data, indent=2))
            self.notify("Copied JSON to clipboard", severity="information")

    async def action_logout(self):
        await self.client.close()
        stack = self.app.screen_stack
        if len(stack) >= 2 and isinstance(stack[-2], RedfishLogin):
            self.app.pop_screen()
        else:
            self.app.push_screen(RedfishLogin())

    async def _activate_action(self, action: ActionTarget):
        body = dict(action.params or {})
        complete = False

        if action.action_info:
            try:
                info_data = await self.client.get(action.action_info)
                body = self._params_from_action_info(info_data)
                complete = True
            except Exception as e:
                self.notify(f"Couldn't fetch ActionInfo for {action.name}: {e}", severity="warning")

        if not complete and action.schema_ref:
            odata_type, raw_key = action.schema_ref
            schema_params = await self._params_from_local_schema(odata_type, raw_key)
            if schema_params:
                body = schema_params
                complete = True

        if not body:
            self.notify(f"No parameter info exposed for {action.name} -- check the BMC's Redfish schema for the required body.", severity="warning")
        elif not complete:
            self.notify(f"{action.name}: only params with inline AllowableValues are shown -- there may be other required fields (e.g. free-text URIs) not listed here.", severity="warning")

        self.code_area.display = True
        self.code_area.query_one("#select", Select).value = "POST"
        self.code_area.query_one("#input", Input).value = self.client.base_url + action.target
        self.code_area.query_one("#code-editor", TextArea).text = json.dumps(body, indent=2)
        self.prefilled_path = None

    def _prefill_edit_panel(self):
        patch_path = self.current_path
        settings = (self.current_data or {}).get("@Redfish.Settings")
        if isinstance(settings, dict):
            settings_path = settings.get("SettingsObject", {}).get("@odata.id")
            if settings_path and settings_path != self.current_path:
                patch_path = settings_path
                self.notify(
                    f"This resource is edited via its Settings object -- "
                    f"redirected PATCH to {settings_path}.",
                    severity="information",
                )

        self.code_area.query_one("#select", Select).value = "PATCH"
        self.code_area.query_one("#input", Input).value = self.client.base_url + patch_path
        self.code_area.query_one("#code-editor", TextArea).text = ""
        self.prefilled_path = self.current_path
        self.code_area.query_one(TabbedContent).active = "edit-tab"

    async def action_toggle_edit(self):
        opening = not self.code_area.display
        self.code_area.display = opening

        if opening and self.current_path and self.current_path != self.prefilled_path:
            self._prefill_edit_panel()

    @work(exclusive=True)
    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id != "send-btn":
            return

        method = self.code_area.query_one("#select", Select).value
        url = self.code_area.query_one("#input", Input).value.strip()
        editor = self.code_area.query_one("#code-editor", TextArea)

        if not method or method is Select.BLANK or not url:
            self.notify("Pick a method and enter a URL", severity="warning")
            return

        path = url[len(self.client.base_url):] if url.startswith(self.client.base_url) else url

        body = None
        text = editor.text.strip()
        if text:
            try:
                body = json.loads(text)
            except json.JSONDecodeError as e:
                self.notify(f"Invalid JSON body: {e}", severity="error")
                return

        if method in WRITE_METHODS:
            confirmed = await self.app.push_screen_wait(ConfirmModal(method, path, body))
            if not confirmed:
                self.notify("Cancelled, nothing sent", severity="warning")
                return
            self._show_response(f"Sending {method} {path} ...")

        etag = None
        if method == "PATCH":
            etag = await self.client.get_etag(path)

        try:
            result = await self.client.request(method, path, body=body, etag=etag)
        except RedfishError as e:
            self.notify(f"{method} {path} -> {e.status_code}: {e}", severity="error")
            detail = json.dumps(e.data, indent=2) if e.data else str(e)
            self._show_response(f"HTTP {e.status_code} -- {method} {path}\n\n{detail}")
            return
        except Exception as e:
            self.notify(f"Request failed: {e}", severity="error")
            self._show_response(f"Request failed -- {method} {path}\n\n{e}")
            return

        self.notify(f"{method} {path} OK", severity="information")
        self._show_response(json.dumps(result, indent=2) if result else "(no content)")

    def _show_response(self, text):
        response_editor = self.code_area.query_one("#response-editor", TextArea)
        response_editor.text = text
        self.code_area.query_one(TabbedContent).active = "response-tab"
        response_editor.focus()

class RedfishApp(App):
    TITLE = "><((('> PoissonRouge"
    CSS = """
    Screen {
      align: center middle;
      layers: below above;
      border: double white;
      width: 100%;
      height: 100%;
    }

    RedfishLogin > Center {
      layer: above;
      width: 25%;
      height: auto;
      border: double white;
      padding: 2 2 1 2;
    
      Button {
        margin: 1;
        width: 100%;
      }
    }

    FishBackground {
      color: red;
      opacity: 50%;
    }

    RedfishBrowser > Horizontal > Tree {
      max-width: 30%;
    }

    Tree, TextArea {
      scrollbar-size-vertical: 0;
    }

    #redfish-tree, #details-pane, #code-area {
      border: round white;
    }

    TabPane {
      padding-top: 1;
    }

    RedfishAction {
      max-height: 50%;

      Vertical {
        Horizontal {
          height: auto;
          Select { width: 10%; }
          Input { width: 1fr; }
          Button { width: auto; }
        }
      }
    }


    """
    def get_system_commands(self, screen: Screen):
        yield from super().get_system_commands(screen)
        if isinstance(screen, RedfishBrowser):
            yield SystemCommand(
                "Toggle edit panel",
                "Show/hide the request editor",
                screen.action_toggle_edit,
            )
            yield SystemCommand(
                "Logout",
                "Disconnect and return to the login screen",
                screen.action_logout,
            )

    async def on_mount(self):
        self.theme = "catppuccin-mocha"
        await self.push_screen(RedfishLogin())

if __name__ == "__main__":
    app = RedfishApp()
    app.run()

