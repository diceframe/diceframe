"""WebUI 路由 handler 子模块。

按域拆分 web_server.py 的 handler：每个子模块含一组 handler 和
对应的 register_xxx(app) 函数，由 web_server.register_routes 调用。
"""
