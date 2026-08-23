import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QTextBrowser

app = QApplication(sys.argv)

text = "（坐直身子，认真思考）嗯…让我想想。我可以陪你聊科学、聊时间旅行理论，或者聊聊这个世界。\n\n如果你想讨论实验数据、验证假设，我也可以帮忙推演。再不然，就当个听众，听你说说最近发生的事也行。\n\n（歪头）不过先说好，我可不会帮你写论文代笔哦。"

def render_markdown(t):
    import html as H
    try:
        import markdown
        r = markdown.markdown(t, extensions=["fenced_code", "tables", "nl2br"], output_format="html5")
    except ImportError as e:
        print("NO markdown:", e)
        return H.escape(t).replace("\n", "<br>")
    if r.startswith("<p>") and r.endswith("</p>") and r.count("<p>") == 1:
        r = r[3:-4]
    return r

rendered = render_markdown(text)
print("=== rendered html ===")
print(rendered)
print("=== len(rendered) =", len(rendered))

b = QTextBrowser()
b.setHtml("<html><body>" + rendered + "</body></html>")
plain = b.toPlainText()
print("=== toPlainText ===")
print(plain)
print("=== len(plain) =", len(plain))
print("=== plain contains all 3 sentences? ===")
print("s1:", "坐直身子" in plain, " s2:", "验证假设" in plain, " s3:", "论文代笔" in plain)
