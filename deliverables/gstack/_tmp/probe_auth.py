import sys, os; sys.path.insert(0,".")
# 用与后端相同的 secret 环境加载
from dotenv import load_dotenv
load_dotenv("../.env")
from app.core.auth import create_jwt_token, _jwt_secret
print("secret prefix:", _jwt_secret()[:6], "len", len(_jwt_secret()))
tok, exp = create_jwt_token("audit_probe", "admin")
open("../deliverables/gstack/_tmp/token.txt","w").write(tok)
print("token len", len(tok), "exp_in", exp)
