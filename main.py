import asyncio
import httpx
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import ListView, ListItem, Label, Static, Header, Footer, Input

API_BASE = "http://localhost:8080"


class MessageItem(Static):
    def __init__(self, message: dict, **kwargs):
        self.msg = message
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        sender = self.msg.get("sender", "unknown")
        time = self.msg.get("time", "")
        display = self.msg.get("display", "")
        msg_type = self.msg.get("type", "text")

        header = f"[{time}] {sender}"
        if msg_type != "text":
            header += f" ({msg_type})"

        yield Label(header, classes="msg-header")
        yield Label(display, classes="msg-body")


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

    #messages-container {
        width: 100%;
        height: 1fr;
        padding: 1 2;
        border: solid $background-lighten-1;
    }

    #messages-container:focus {
        border: solid $accent;
    }

    #messages-empty {
        width: 100%;
        height: 100%;
        content-align: center middle;
        color: $text-muted;
    }

    .msg-header {
        color: $text-accent;
        text-style: bold;
        margin-top: 1;
    }

    .msg-body {
        margin-bottom: 1;
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
                with VerticalScroll(id="messages-container"):
                    yield Static("Select a contact to view messages", id="messages-empty")
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
        self._populate_list()

    def _contact_name(self, contact: dict) -> str:
        name = contact.get("name")
        if name:
            return name
        jid = contact.get("jid", "")
        return jid.split("@")[0] if "@" in jid else jid

    def _populate_list(self) -> None:
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
        self._populate_list()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search-input":
            self.query_one("#contacts-list", ListView).focus()

    async def action_focus_contacts(self) -> None:
        self.query_one("#contacts-list", ListView).focus()

    async def action_focus_messages(self) -> None:
        self.query_one("#messages-container", VerticalScroll).focus()

    async def action_focus_search(self) -> None:
        self.query_one("#search-input", Input).focus()

    async def action_refresh(self) -> None:
        messages_container = self.query_one("#messages-container", VerticalScroll)
        if messages_container.has_focus:
            if self.current_contact is None:
                return
            await self._sync_and_load(self.current_contact)
        else:
            await self.action_refresh_contacts()

    async def _sync_and_load(self, contact: dict) -> None:
        jid = contact.get("jid", "")
        name = self._contact_name(contact)
        container = self.query_one("#messages-container", VerticalScroll)
        container.remove_children()
        loading = Static(f"Syncing messages for {name}...")
        await container.mount(loading)

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{API_BASE}/messages/sync",
                    params={"jid": jid, "limit": 50},
                    timeout=30.0,
                )
                resp.raise_for_status()
        except Exception as e:
            container.remove_children()
            await container.mount(Static(f"Sync failed: {e}"))
            return

        await self._load_messages(contact)

    async def _load_messages(self, contact: dict) -> None:
        jid = contact.get("jid", "")
        name = self._contact_name(contact)
        container = self.query_one("#messages-container", VerticalScroll)
        container.remove_children()

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
            await container.mount(Static(f"Error loading messages: {e}"))
            return

        if not self.messages:
            await container.mount(Static(f"No messages with {name}."))
            return

        for msg in self.messages:
            await container.mount(MessageItem(msg))

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
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
