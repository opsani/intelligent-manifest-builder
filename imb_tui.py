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

    ## K8s prompt values from default context
    async def prompt_k8s_active_context(self, kubeconfigPath='', context='', cluster=''):
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

        yn_dialog = Dialog(
            title='Use Active Context?',
            body=Window(
                FormattedTextControl('Use the currently active context values?'), 
                align=WindowAlign.CENTER,
                height=1,
            ),
            buttons=[
                Button(text="Yes", handler=yes_handler),
                Button(text="No", handler=no_handler),
            ],
            modal=False,
        )
        # disable a_reverse style applied to dialogs
        yn_dialog.container.container.content.style=""
        self.app_frame.body = HSplit([
            Window(FormattedTextControl('Kubernetes Config', style='bold'), align=WindowAlign.CENTER, height=1),
            Window(),
            Window(FormattedTextControl('Kubeconfig Path:'), height=1),
            Window(FormattedTextControl(kubeconfigPath), align=WindowAlign.CENTER),
            Window(FormattedTextControl('Active Context:'), height=1),
            Window(FormattedTextControl(context), align=WindowAlign.CENTER),
            Window(FormattedTextControl('Active Cluster:'), height=1),
            Window(FormattedTextControl(cluster), align=WindowAlign.CENTER),
            yn_dialog,
        ])
        self.app.invalidate()
        self.app.layout.focus(self.app_frame)
        await input_done.wait()
        return result

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

    async def prompt_text_input(self, title, prompt, initial_text = ''):
        result = None
        input_done = asyncio.Event()

        def accept(buf) -> bool:
            get_app().layout.focus(ok_button)
            return True  # Keep text.

        def ok_handler() -> None:
            nonlocal result
            result=textfield.text
            input_done.set()

        def cancel_handler() -> None:
            input_done.set()

        ok_button = Button(text='Ok', handler=ok_handler)
        cancel_button = Button(text='Cancel', handler=cancel_handler)
        textfield = TextArea(text=initial_text, multiline=False, accept_handler=accept)

        dialog = Dialog(
            title=title,
            body=HSplit(
                [Window(FormattedTextControl(text=prompt), align=WindowAlign.CENTER, dont_extend_height=True), textfield,],
                padding=Dimension(preferred=1, max=1),
            ),
            buttons=[ok_button, cancel_button],
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

    async def prompt_text_two_input(self, title, prompt1, prompt2, initial_text1 = '', initial_text2 = ''):
        result = None
        input_done = asyncio.Event()

        def accept(buf) -> bool:
            get_app().layout.focus(ok_button)
            return True  # Keep text.

        def ok_handler() -> None:
            nonlocal result
            result = ( textfield1.text, textfield2.text )
            input_done.set()

        def cancel_handler() -> None:
            input_done.set()

        ok_button = Button(text='Ok', handler=ok_handler)
        cancel_button = Button(text='Cancel', handler=cancel_handler)

        textfield1 = TextArea(text=initial_text1, multiline=False, accept_handler=accept)
        textfield2 = TextArea(text=initial_text2, multiline=False, accept_handler=accept)

        dialog = Dialog(
            title=title,
            body=HSplit(
                [
                    Window(FormattedTextControl(text=prompt1), align=WindowAlign.CENTER, dont_extend_height=True), textfield1,
                    Window(FormattedTextControl(text=prompt2), align=WindowAlign.CENTER, dont_extend_height=True), textfield2
                ],
                padding=Dimension(preferred=1, max=1),
            ),
            buttons=[ok_button, cancel_button],
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

    async def prompt_text_three_input(self, title, prompt1, prompt2, prompt3, initial_text1 = '', initial_text2 = '', initial_text3 = ''):
        result = None
        input_done = asyncio.Event()

        def accept(buf) -> bool:
            get_app().layout.focus(ok_button)
            return True  # Keep text.

        def ok_handler() -> None:
            nonlocal result
            result = ( textfield1.text, textfield2.text, textfield3.text )
            input_done.set()

        def cancel_handler() -> None:
            input_done.set()

        ok_button = Button(text='Ok', handler=ok_handler)
        cancel_button = Button(text='Cancel', handler=cancel_handler)

        textfield1 = TextArea(text=initial_text1, multiline=False, accept_handler=accept)
        textfield2 = TextArea(text=initial_text2, multiline=False, accept_handler=accept)
        textfield3 = TextArea(text=initial_text3, multiline=False, accept_handler=accept)

        dialog = Dialog(
            title=title,
            body=HSplit(
                [
                    Window(FormattedTextControl(text=prompt1), align=WindowAlign.CENTER, dont_extend_height=True), textfield1,
                    Window(FormattedTextControl(text=prompt2), align=WindowAlign.CENTER, dont_extend_height=True), textfield2,
                    Window(FormattedTextControl(text=prompt3), align=WindowAlign.CENTER, dont_extend_height=True), textfield3
                ],
                padding=Dimension(preferred=1, max=1),
            ),
            buttons=[ok_button, cancel_button],
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

    async def prompt_text_four_input(self, title, prompt1, prompt2, prompt3, prompt4, initial_text1 = '', initial_text2 = '', initial_text3 = '', initial_text4 = ''):
        result = None
        input_done = asyncio.Event()

        def accept(buf) -> bool:
            get_app().layout.focus(ok_button)
            return True  # Keep text.

        def ok_handler() -> None:
            nonlocal result
            result = ( textfield1.text, textfield2.text, textfield3.text, textfield4.text )
            input_done.set()

        def cancel_handler() -> None:
            input_done.set()

        ok_button = Button(text='Ok', handler=ok_handler)
        cancel_button = Button(text='Cancel', handler=cancel_handler)

        textfield1 = TextArea(text=initial_text1, multiline=False, accept_handler=accept)
        textfield2 = TextArea(text=initial_text2, multiline=False, accept_handler=accept)
        textfield3 = TextArea(text=initial_text3, multiline=False, accept_handler=accept)
        textfield4 = TextArea(text=initial_text4, multiline=False, accept_handler=accept)

        dialog = Dialog(
            title=title,
            body=HSplit(
                [
                    Window(FormattedTextControl(text=prompt1), align=WindowAlign.CENTER, dont_extend_height=True), textfield1,
                    Window(FormattedTextControl(text=prompt2), align=WindowAlign.CENTER, dont_extend_height=True), textfield2,
                    Window(FormattedTextControl(text=prompt3), align=WindowAlign.CENTER, dont_extend_height=True), textfield3,
                    Window(FormattedTextControl(text=prompt4), align=WindowAlign.CENTER, dont_extend_height=True), textfield4
                ],
                padding=Dimension(preferred=1, max=1),
            ),
            buttons=[ok_button, cancel_button],
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

        def cancel_handler() -> None:
            input_done.set()

        radio_list = RadioList(list(enumerate(values)))
        dialog = Dialog(
            title=title,
            body=HSplit(
                [Label(text=HTML("    <b>{}</b>".format(header)), dont_extend_height=True), radio_list,], padding=1
            ),
            buttons=[
                Button(text='Ok', handler=ok_handler),
                Button(text='Cancel', handler=cancel_handler),
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

        def cancel_handler() -> None:
            input_done.set()

        cb_list = CheckboxList(list(enumerate(values)))
        dialog = Dialog(
            title=title,
            body=HSplit([Label(text=HTML("    <b>{}</b>".format(header)), dont_extend_height=True), cb_list,], padding=1),
            buttons=[
                Button(text='Ok', handler=ok_handler),
                Button(text='Cancel', handler=cancel_handler),
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