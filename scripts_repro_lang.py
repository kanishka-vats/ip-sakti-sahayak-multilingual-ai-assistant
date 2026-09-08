from fastapi.testclient import TestClient
from src.api.main import app
c = TestClient(app)
for q in ["टीकेडीएल क्या है?",
          "पेटेंट क्या है?",
          "नमस्ते",
          "tkdl kya hai?",
          "patent kaise file karte hain india me?"]:
    r = c.post("/api/query", json={"query": q}).json()
    print(f"Q:{q[:42]} abst={r['abstained']} conf={r['confidence']} "
          f"cites={len(r['citations'])} head={r['answer'][:70].replace(chr(10), ' ')}")
