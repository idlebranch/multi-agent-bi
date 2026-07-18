"""
生成 BI Agent 的状态图
输出:
- graph_mermaid.txt(Mermaid 源码,可粘贴到 mermaid.live 查看)
- graph_diagram.png(PNG 图片,如果环境支持的话)
"""
from src.graph import app

# 方法 1: 拿 mermaid 源码
mermaid_code = app.get_graph().draw_mermaid()
with open("graph_mermaid.txt", "w", encoding="utf-8") as f:
    f.write(mermaid_code)
print("Mermaid 源码已存到 graph_mermaid.txt")
print("可以贴到 https://mermaid.live 查看效果\n")
print("--- Mermaid 源码预览 ---")
print(mermaid_code)
print()

# 方法 2: 直接生成 PNG(需要联网,用 mermaid.ink API)
try:
    png_bytes = app.get_graph().draw_mermaid_png()
    with open("graph_diagram.png", "wb") as f:
        f.write(png_bytes)
    print("PNG 图片已存到 graph_diagram.png")
except Exception as e:
    print(f"PNG 生成失败(可能是网络问题): {e}")
    print("不影响,Mermaid 源码已存,可手动到 mermaid.live 看")
