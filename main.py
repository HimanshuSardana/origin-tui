import asyncio
import httpx
import subprocess
import shutil
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.message import Message
from textual.widgets import ListView, ListItem, Label, Static, Header, Footer, Input, TextArea

API_BASE = "http://localhost:8080"


def copy_to_clipboard(text: str) -> None:
    if shutil.which("wl-copy"):
        subprocess.run(["wl-copy"], input=text.encode(), check=False)
    elif shutil.which("xclip"):
        subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=False)
    elif shutil.which("xsel"):
        subprocess.run(["xsel", "--clipboard", "--input"], input=text.encode(), check=False)
    elif shutil.which("pbcopy"):
        subprocess.run(["pbcopy"], input=text.encode(), check=False)


class ComposeSend(Message):
    pass


class ComposeBlur(Message):
    pass


class ComposeArea(TextArea):
    def key_ctrl_enter(self, event: Key) -> None:
        event.stop()
        self.post_message(ComposeSend())

    def key_ctrl_s(self, event: Key) -> None:
        event.stop()
        self.post_message(ComposeSend())

    def key_escape(self, event: Key) -> None:
        event.stop()
        self.post_message(ComposeBlur())


class OriginApp(App):
    CSS = """
    Screen { align: center middle; }

    #main-layout {
        width: 100%;
        height: 100%;
    }

    #contacts-panel {
        width: 30%;
        height: 100%;
        border-right: solid $primary;
    }

    #search-input {
        width: 100%;
        height: auto;
        border: solid $primary-darken-2;
        padding: 0 1;
    }

    #search-input:focus {
        border: solid $accent;
    }

    #contacts-list {
        width: 100%;
        height: 1fr;
    }

    #messages-panel {
        width: 70%;
        height: 100%;
    }

    #messages-list {
        width: 100%;
        height: 1fr;
        border: solid $background-lighten-1;
    }

    #messages-list:focus {
        border: solid $accent;
    }

    #compose-area {
        width: 100%;
        height: 3;
        border: solid $primary-darken-2;
        padding: 0 1;
    }

    #compose-area:focus {
        border: solid $accent;
    }

    .msg-row {
        width: 100%;
        height: auto;
        padding: 0 1;
    }

    .msg-time {
        color: $text-muted;
        text-style: dim;
    }

    .msg-sender {
        color: $text-accent;
        text-style: bold;
    }

    .msg-type {
        color: $warning;
        text-style: italic;
    }

    .msg-display {
        color: $text;
        width: 100%;
    }

    .msg-url {
        color: $success;
        text-style: underline;
    }

    ListView:focus {
        border: solid $accent;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("1", "focus_contacts", "Contacts"),
        ("2", "focus_messages", "Messages"),
        ("slash", "focus_search", "Search"),
        ("c", "copy_message", "Copy"),
    ]

    def __init__(self):
        self.contacts: list[dict] = []
        self.displayed_contacts: list[dict] = []
        self.messages: list[dict] = []
        self.current_contact: dict | None = None
        self._search_task: asyncio.Task | None = None
        super().__init__(ansi_color=True)

    def compose(self) -> ComposeResult:
        yield Header(name="Origin", show_clock=False)
        with Horizontal(id="main-layout"):
            with Vertical(id="contacts-panel"):
                yield Static("Contacts", classes="title")
                yield Input(placeholder="Search contacts...", id="search-input")
                yield ListView(id="contacts-list")
            with Vertical(id="messages-panel"):
                yield Static("Messages", classes="title")
                yield ListView(id="messages-list")
                yield ComposeArea(id="compose-area", disabled=True)
        yield Footer()

    async def on_mount(self) -> None:
        await self.action_refresh_contacts()
        self.query_one("#contacts-list", ListView).focus()

    async def action_refresh_contacts(self) -> None:
        list_view = self.query_one("#contacts-list", ListView)
        list_view.clear()
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{API_BASE}/contacts", timeout=10.0)
                resp.raise_for_status()
                self.contacts = resp.json()
        except Exception as e:
            list_view.append(ListItem(Label(f"Error: {e}")))
            self.displayed_contacts = []
            return

        self.displayed_contacts = self.contacts[:]
        self._populate_contacts()

    def _contact_name(self, contact: dict) -> str:
        name = contact.get("name")
        if name:
            return name
        jid = contact.get("jid", "")
        return jid.split("@")[0] if "@" in jid else jid

    def _populate_contacts(self) -> None:
        list_view = self.query_one("#contacts-list", ListView)
        list_view.clear()
        for contact in self.displayed_contacts:
            list_view.append(ListItem(Label(self._contact_name(contact))))

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "search-input":
            return
        if self._search_task is not None:
            self._search_task.cancel()
        self._search_task = asyncio.create_task(
            self._debounced_search(event.value)
        )

    async def _debounced_search(self, query: str) -> None:
        await asyncio.sleep(0.2)
        query = query.lower()
        if not query:
            self.displayed_contacts = self.contacts[:]
        else:
            self.displayed_contacts = [
                c for c in self.contacts if query in self._contact_name(c).lower()
            ]
        self._populate_contacts()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search-input":
            self.query_one("#contacts-list", ListView).focus()

    async def action_focus_contacts(self) -> None:
        self.query_one("#contacts-list", ListView).focus()

    async def action_focus_messages(self) -> None:
        self.query_one("#messages-list", ListView).focus()

    async def action_focus_search(self) -> None:
        self.query_one("#search-input", Input).focus()

    async def action_copy_message(self) -> None:
        messages_list = self.query_one("#messages-list", ListView)
        if not messages_list.has_focus:
            return
        index = messages_list.index
        if index is None or index < 0 or index >= len(self.messages):
            return
        msg = self.messages[index]
        text = msg.get("display", "")
        if text:
            copy_to_clipboard(text.strip())
            self.notify("Copied to clipboard", severity="information", timeout=1.5)

    async def action_send_message(self) -> None:
        if self.current_contact is None:
            return
        text_area = self.query_one("#compose-area", ComposeArea)
        message = text_area.text.strip()
        if not message:
            return
        jid = self.current_contact.get("jid", "")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{API_BASE}/messages/send",
                    json={"jid": jid, "message": message},
                    timeout=10.0,
                )
                resp.raise_for_status()
        except Exception as e:
            messages_list = self.query_one("#messages-list", ListView)
            messages_list.clear()
            await messages_list.mount(ListItem(Label(f"Send failed: {e}")))
            return
        text_area.text = ""
        await self._load_messages(self.current_contact)

    async def on_compose_send(self, event: ComposeSend) -> None:
        await self.action_send_message()

    async def on_compose_blur(self, event: ComposeBlur) -> None:
        await self.action_focus_messages()

    async def action_refresh(self) -> None:
        messages_list = self.query_one("#messages-list", ListView)
        if messages_list.has_focus:
            if self.current_contact is None:
                return
            await self._sync_and_load(self.current_contact)
        else:
            await self.action_refresh_contacts()

    async def _sync_and_load(self, contact: dict) -> None:
        jid = contact.get("jid", "")
        name = self._contact_name(contact)
        messages_list = self.query_one("#messages-list", ListView)
        messages_list.clear()
        await messages_list.mount(ListItem(Label(f"Syncing messages for {name}...")))

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{API_BASE}/messages/sync",
                    params={"jid": jid, "limit": 50},
                    timeout=30.0,
                )
                resp.raise_for_status()
        except Exception as e:
            messages_list.clear()
            await messages_list.mount(ListItem(Label(f"Sync failed: {e}")))
            return

        await self._load_messages(contact)

    async def _load_messages(self, contact: dict) -> None:
        jid = contact.get("jid", "")
        name = self._contact_name(contact)
        messages_list = self.query_one("#messages-list", ListView)
        messages_list.clear()
        compose = self.query_one("#compose-area", ComposeArea)
        compose.disabled = False

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{API_BASE}/messages",
                    params={"jid": jid, "limit": 100},
                    timeout=10.0,
                )
                resp.raise_for_status()
                self.messages = resp.json()
        except Exception as e:
            await messages_list.mount(ListItem(Label(f"Error loading messages: {e}")))
            return

        if not self.messages:
            await messages_list.mount(ListItem(Label(f"No messages with {name}.")))
            return

        for msg in self.messages:
            item = self._build_message_item(msg)
            await messages_list.mount(item)

    def _build_message_item(self, msg: dict) -> ListItem:
        time = msg.get("time", "")
        sender = msg.get("sender", "unknown")
        display = msg.get("display", "")
        msg_type = msg.get("type", "text")

        lines: list[str] = []
        header = f"[{time}] {sender}"
        if msg_type != "text":
            header += f" [{msg_type}]"
        lines.append(header)
        lines.append(display)

        text = "\n".join(lines)
        return ListItem(Label(text, classes="msg-row"))

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "contacts-list":
            index = event.index
            if index is None or index < 0 or index >= len(self.displayed_contacts):
                return
            contact = self.displayed_contacts[index]
            self.current_contact = contact
            await self._load_messages(contact)


def main():
    app = OriginApp()
    app.run()


if __name__ == "__main__":
    main()
