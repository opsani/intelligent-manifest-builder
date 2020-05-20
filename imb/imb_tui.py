#!/usr/bin/env python3

import asyncio
import atexit
import sys
from typing import Iterable, Union

assert sys.version_info >= (3, 6, 1), "Must be running on python >= 3.6.1. Found: {}".format(sys.version)

# TODO: remove unused imports
from prompt_toolkit import Application, HTML
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.application.current import get_app
from prompt_toolkit.widgets import Button, CheckboxList, Dialog, Frame, Label, RadioList, TextArea
from prompt_toolkit.layout.containers import HSplit, Window, WindowAlign
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.key_binding import KeyBindings

class ImbTuiResult:
    def __init__(self):
        self.back_selected = False
        self.other_selected = False
        self.value = None

# TODO: refactor redundant logic (eg. input_done = asyncio.Event())
class ImbTui:
    def __init__(self):
        self.init_done = asyncio.Event()

    async def start_ui(self):
        kb = KeyBindings()
        @kb.add('escape')
        def _(event):
            for t in asyncio.Task.all_tasks():
                if 'Imb.main()' in str(t) and not t.done():
                    t.cancel()

        # Allow member functions to access this frame to allow switching screens
        self.app_frame = Frame(title='Opsani Intelligent Manifest Builder', body=Window())
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

    async def prompt_yn(self, title, prompt, disable_back=False, allow_other=False, other_button_text="Other"):
        result = ImbTuiResult()
        input_done = asyncio.Event()

        def yes_handler():
            result.value = True
            input_done.set()

        def no_handler():
            result.value = False
            input_done.set()

        def back_handler():
            result.back_selected = True
            input_done.set()

        def other_handler():
            result.other_selected = True
            input_done.set()

        buttons=[
            Button(text="Yes", handler=yes_handler),
            Button(text="No", handler=no_handler)
        ]
        if not disable_back:
            buttons.append(Button(text="Back", handler=back_handler))

        if allow_other:
            buttons.append(Button(text=other_button_text, handler=other_handler))

        yn_dialog = Dialog(
            title=title,
            body=Window(
                FormattedTextControl(prompt), 
                height=1, 
                align=WindowAlign.CENTER
            ),
            buttons=buttons,
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

    # prompt-toolkit supports line wrapping but it disregards breaking of words across lines
    # In cases where the text will span multiple lines, it should be divided up into an
    # array of prompt lines where each line should be short enough to fit on the screen
    async def prompt_ok(self, title, prompt: Union[str, Iterable[str]]):
        result = ImbTuiResult()
        input_done = asyncio.Event()

        dialog_body = []
        if isinstance(prompt, str):
            dialog_body.append(Window(FormattedTextControl(prompt),height=1, align=WindowAlign.CENTER))
        else:
            for line in prompt:
                dialog_body.append(Window(FormattedTextControl(line),height=1, align=WindowAlign.CENTER))

        def ok_handler():
            result.value = True
            input_done.set()

        def back_handler():
            result.back_selected = True
            input_done.set()

        ok_dialog = Dialog(
            title=title,
            body=HSplit(dialog_body),
            buttons=[
                Button(text="Ok", handler=ok_handler),
                Button(text="Back", handler=back_handler),
            ],
            modal=False,
        )
        # disable a_reverse style applied to dialogs
        ok_dialog.container.container.content.style=""
        self.app_frame.body = HSplit([
            Window(),
            ok_dialog,
            Window(),
        ])
        self.app.invalidate()
        self.app.layout.focus(self.app_frame)
        await input_done.wait()
        return result

    async def prompt_text_input(self, title, prompts, allow_other=False):
        result = ImbTuiResult()
        input_done = asyncio.Event()
        text_fields = []
        dialog_hsplit_content = []

        def accept(buf) -> bool:
            get_app().layout.focus(ok_button)
            return True  # Keep text.

        def ok_handler() -> None:
            if len(prompts) == 1:
                result.value = text_fields[0].text
            else:
                result.value = tuple( t.text for t in text_fields )
            input_done.set()

        def back_handler() -> None:
            result.back_selected = True
            input_done.set()

        def other_handler() -> None:
            result.other_selected = True
            input_done.set()

        ok_button = Button(text='Ok', handler=ok_handler) # capture ref to allow accept handler to focus it
        buttons = [
            ok_button,
            Button(text='Back', handler=back_handler)
        ]
        if allow_other:
            buttons.append(Button(text='Other', handler=other_handler))

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
            buttons=buttons,
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

    async def prompt_multiline_text_input(self, title, prompt, initial_text=''):
        result = ImbTuiResult()
        input_done = asyncio.Event()

        def ok_handler() -> None:
            result.value = textfield.text
            input_done.set()

        def back_handler() -> None:
            result.back_selected = True
            input_done.set()

        ok_button = Button(text='Ok', handler=ok_handler)
        back_button = Button(text='Back', handler=back_handler)

        textfield = TextArea(text=initial_text, multiline=True, scrollbar=True)

        dialog = Dialog(
            title=title,
            body=HSplit(
                [
                    Window(FormattedTextControl(text=prompt), align=WindowAlign.CENTER, dont_extend_height=True), 
                    textfield,
                ],
                padding=Dimension(preferred=1, max=1),
            ),
            buttons=[ok_button, back_button],
            modal=False,
        )

        dialog.container.container.content.style=""
        self.app_frame.body = HSplit([
            dialog,
        ])
        self.app.invalidate()
        self.app.layout.focus(self.app_frame)
        await input_done.wait()
        return result

    async def prompt_multiline_text_output(self, title, text=''):
        result = ImbTuiResult()
        input_done = asyncio.Event()

        def ok_handler() -> None:
            input_done.set()

        def back_handler() -> None:
            result.back_selected = True
            input_done.set()

        ok_button = Button(text='Ok', handler=ok_handler)
        back_button = Button(text='Back', handler=back_handler)

        dialog = Dialog(
            title=title,
            body=TextArea(text=text, multiline=True, scrollbar=True, read_only=True),
            buttons=[ok_button, back_button],
            modal=False,
        )

        dialog.container.container.content.style=""
        self.app_frame.body = HSplit([
            dialog,
        ])
        self.app.invalidate()
        self.app.layout.focus(self.app_frame)
        await input_done.wait()
        return result

    async def prompt_radio_list(self, values, title, header, allow_other=True):
        result = ImbTuiResult()
        input_done = asyncio.Event()

        def ok_handler() -> None:
            result.value = radio_list.current_value
            input_done.set()

        def back_handler() -> None:
            result.back_selected = True
            input_done.set()

        def other_handler() -> None:
            result.other_selected = True
            input_done.set()

        buttons = [
            Button(text='Ok', handler=ok_handler),
            Button(text='Back', handler=back_handler),
        ]
        if allow_other:
            buttons.append(Button(text='Other', handler=other_handler))

        radio_list = RadioList(list(enumerate(values)))
        dialog = Dialog(
            title=title,
            body=HSplit(
                [Label(text=HTML("    <b>{}</b>".format(header)), dont_extend_height=True), radio_list,], padding=1
            ),
            buttons=buttons,
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

    async def prompt_check_list(self, values, title, header, allow_other=True):
        result = ImbTuiResult()
        input_done = asyncio.Event()

        def ok_handler() -> None:
            result.value = cb_list.current_values
            input_done.set()

        def back_handler() -> None:
            result.back_selected = True
            input_done.set()

        def other_handler() -> None:
            result.other_selected = True
            input_done.set()

        buttons = [
            Button(text='Ok', handler=ok_handler),
            Button(text='Back', handler=back_handler),
        ]
        if allow_other:
            buttons.append(Button(text='Other', handler=other_handler))

        cb_list = CheckboxList(list(enumerate(values)))
        dialog = Dialog(
            title=title,
            body=HSplit([Label(text=HTML("    <b>{}</b>".format(header)), dont_extend_height=True), cb_list,], padding=1),
            buttons=buttons,
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