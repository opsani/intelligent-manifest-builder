#!/usr/bin/env python3

import asyncio
import sys
assert sys.version_info >= (3, 6, 1), "Must be running on python >= 3.6.1. Found: {}".format(sys.version)

# TODO: remove unused imports
from prompt_toolkit import Application, HTML
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.application.current import get_app
from prompt_toolkit.widgets import Button, CheckboxList, Dialog, Frame, Label, RadioList, TextArea
from prompt_toolkit.layout.containers import HSplit, Window, WindowAlign
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.key_binding import KeyBindings

# TODO: refactor redundant logic (eg. input_done = asyncio.Event())
class ImbTui:
    def __init__(self):
        self.init_done = asyncio.Event()

    async def start_ui(self):
        kb = KeyBindings()
        @kb.add('escape')
        def _(event):
            event.app.exit()
            cur_task = asyncio.Task.current_task()
            for t in asyncio.Task.all_tasks():
                if t != cur_task and not t.done():
                    t.cancel()

        # Allow member functions to access this frame to allow switching screens
        self.app_frame = Frame(title='Intelligent Manifest Builder', body=Window())
        self.app = Application(
            full_screen=True,
            key_bindings=kb,
            layout=Layout(HSplit([
                self.app_frame,
                Label('Press ESC to exit')
            ]))
        )
        self.init_done.set()
        await self.app.run_async(set_exception_handler=False)

    async def stop_ui(self):
        self.app.exit()

    async def promt_yn(self, title, prompt):
        result = None
        input_done = asyncio.Event()

        def yes_handler():
            nonlocal result
            result = True
            input_done.set()

        def no_handler():
            nonlocal result
            result = False
            input_done.set()

        def back_handler():
            input_done.set()

        yn_dialog = Dialog(
            title=title,
            body=Window(
                FormattedTextControl(prompt), 
                height=1, 
                align=WindowAlign.CENTER
            ),
            buttons=[
                Button(text="Yes", handler=yes_handler),
                Button(text="No", handler=no_handler),
                Button(text="Back", handler=back_handler),
            ],
            modal=False,
        )
        # disable a_reverse style applied to dialogs
        yn_dialog.container.container.content.style=""
        self.app_frame.body = HSplit([
            Window(),
            yn_dialog,
            Window(),
        ])
        self.app.invalidate()
        self.app.layout.focus(self.app_frame)
        await input_done.wait()
        return result

    async def prompt_text_input(self, title, prompts):
        if len(prompts) == 1:
            result = None
        else:
            result = tuple(None for _ in prompts)
            
        input_done = asyncio.Event()
        text_fields = []
        dialog_hsplit_content = []

        def accept(buf) -> bool:
            get_app().layout.focus(ok_button)
            return True  # Keep text.

        def ok_handler() -> None:
            nonlocal result
            if len(prompts) == 1:
                result = text_fields[0].text
            else:
                result = tuple( t.text for t in text_fields )
            input_done.set()

        def back_handler() -> None:
            input_done.set()

        ok_button = Button(text='Ok', handler=ok_handler)
        back_button = Button(text='Back', handler=back_handler)

        for p in prompts:
            text_field = TextArea(text=p.get('initial_text', ''), multiline=False, accept_handler=accept)
            text_fields.append(text_field)
            dialog_hsplit_content.extend([
                Window(FormattedTextControl(text=p['prompt']), align=WindowAlign.CENTER, dont_extend_height=True), 
                text_field
            ])

        dialog = Dialog(
            title=title,
            body=HSplit(dialog_hsplit_content,
                padding=Dimension(preferred=1, max=1),
            ),
            buttons=[ok_button, back_button],
            modal=False,
        )
        dialog.container.container.content.style=""
        self.app_frame.body = HSplit([
            Window(),
            dialog,
            Window(),
        ])
        self.app.invalidate()
        self.app.layout.focus(self.app_frame)
        await input_done.wait()
        return result

    async def prompt_radio_list(self, values, title, header):
        result = None
        input_done = asyncio.Event()

        def ok_handler() -> None:
            nonlocal result
            result=radio_list.current_value
            input_done.set()

        def back_handler() -> None:
            input_done.set()

        radio_list = RadioList(list(enumerate(values)))
        dialog = Dialog(
            title=title,
            body=HSplit(
                [Label(text=HTML("    <b>{}</b>".format(header)), dont_extend_height=True), radio_list,], padding=1
            ),
            buttons=[
                Button(text='Ok', handler=ok_handler),
                Button(text='Back', handler=back_handler),
            ],
            modal=False,
        )
        # disable a_reverse style applied to dialogs
        dialog.container.container.content.style=""
        self.app_frame.body = HSplit([
            # Window(FormattedTextControl(HTML('<b>Kubernetes Config</b>')), align=WindowAlign.CENTER, height=1), # TODO: screen header
            Window(),
            dialog,
            Window()
        ])
        self.app.invalidate()
        self.app.layout.focus(self.app_frame)
        await input_done.wait()
        return result

    async def prompt_check_list(self, values, title, header):
        result = None
        input_done = asyncio.Event()

        def ok_handler() -> None:
            nonlocal result
            result = cb_list.current_values
            input_done.set()

        def back_handler() -> None:
            input_done.set()

        cb_list = CheckboxList(list(enumerate(values)))
        dialog = Dialog(
            title=title,
            body=HSplit([Label(text=HTML("    <b>{}</b>".format(header)), dont_extend_height=True), cb_list,], padding=1),
            buttons=[
                Button(text='Ok', handler=ok_handler),
                Button(text='Back', handler=back_handler),
            ],
            modal=False,
        )
        # disable a_reverse style applied to dialogs
        dialog.container.container.content.style=""
        self.app_frame.body = HSplit([
            Window(),
            dialog,
            Window()
        ])
        self.app.invalidate()
        self.app.layout.focus(self.app_frame)
        await input_done.wait()
        return result