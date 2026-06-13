import os
import loguru
import gradio as gr
import threading
from argparse import Namespace


def _load_app_css() -> str:
    css_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets",
        "style.css",
    )
    try:
        with open(css_path, "r", encoding="utf-8") as file:
            return file.read()
    except OSError as exc:
        loguru.logger.warning(f"加载样式文件失败: {css_path} | {exc}")
        return ""


def exit_app_ui():
    loguru.logger.info("程序退出")
    threading.Timer(2.0, lambda: os._exit(0)).start()
    gr.Info("⚠️ 程序将在弹出Error提示后退出 ⚠️")
    return


def ticker_cmd(args: Namespace):
    from tab.go import go_tab
    from tab.problems import problems_tab
    from tab.settings import setting_tab_v2
    from tab.log import log_tab

    from util.LogConfig import loguru_config
    from util import LOG_DIR

    loguru_config(LOG_DIR, "app.log", enable_console=True, file_colorize=False)

    header = """
    # B 站会员购抢票🌈

    ⚠️此项目完全开源免费 （[项目地址](https://github.com/mikumifa/biliTickerBuy)），切勿进行盈利，所造成的后果与本人无关。
    """
    app_css = _load_app_css()

    with gr.Blocks(
        title="biliTickerBuy",
        css=app_css,
        head="""<script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>""",
    ) as demo:
        gr.Markdown(header)
        with gr.Tabs() as tabs:
            with gr.Tab("生成配置", id="settings"):
                settings_mount = gr.Column()
            with gr.Tab("操作抢票", id="go"):
                go_handles = go_tab(demo)
            with gr.Tab("项目说明", id="problems"):
                problems_tab()
            with gr.Tab("日志查看", id="logs"):
                log_tab()

        with settings_mount:
            setting_tab_v2(go_handles=go_handles, tabs=tabs, demo=demo)

    is_docker = os.path.exists("/.dockerenv") or os.environ.get("BTB_DOCKER") == "1"
    demo.launch(
        share=args.share or is_docker,
        inbrowser=not is_docker,
        server_name=args.server_name,
        server_port=args.port,
    )
