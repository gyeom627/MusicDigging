# PythonAnywhere의 Web 탭 → WSGI configuration file 내용을 이것으로 바꾼다.
# YOURNAME을 PythonAnywhere 사용자명으로 바꿀 것. 키는 여기 적지 말고 ~/digger/.env에 둔다.
import sys

project_home = "/home/YOURNAME/digger"
if project_home not in sys.path:
    sys.path.insert(0, project_home)

from app import app as application  # noqa: E402,F401
