import json, re, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

ALERTAS = {
    "88412|126579742": [{"Wo": "88412|126579742", "ConsolidadoNum": "105012-20260916-mEeF+Ow/Dh", "Fornecedor": "137628"}],
    "02235|230771207": [{"Wo": "02235|230771207", "ConsolidadoNum": "082026-20260806-3YeQ", "Fornecedor": "1000835"}],
}
MSGS = {
    "105012-20260916-mEeF+Ow/Dh": [
        {"Wo": "88412|126579742", "Matnr": "000000000022026169", "MaterialText": "CABO", "Lifnr": "0000137628", "QtdFaltante": "2.000", "Mensagem": "Estoque indisponível"},
        {"Wo": "88412|126579742", "Matnr": "000000000011000042", "MaterialText": "CONECTOR", "Lifnr": "0000137628", "QtdFaltante": "10.000", "Mensagem": "Estoque indisponível"}],
}
PAGINA = """<html><body>Launchpad falso<script>
window.sap = {ushell:{Container:{}}, ui:{core:{Component:{registry:{forEach(f){f({getManifestEntry(){
  return {id:'monitor.alertas', dataSources:{mainService:{uri:'/srv/', type:'OData', settings:{odataVersion:'2.0'}}}}}})}}}}}};
function corpoBatch(gets){
  return gets.map(g=>'--b\\r\\nContent-Type: application/http\\r\\n\\r\\nGET '+g+' HTTP/1.1\\r\\nAccept: application/json\\r\\n\\r\\n\\r\\n').join('')+'--b--';
}
async function batch(gets){
  await fetch('/srv/$batch',{method:'POST',headers:{'Content-Type':'multipart/mixed; boundary=b'},body:corpoBatch(gets)});
}
function batchXHR(gets){
  return new Promise(ok=>{const x=new XMLHttpRequest(); x.open('POST','/srv/$batch'); x.setRequestHeader('Content-Type','multipart/mixed; boundary=b'); x.onloadend=ok; x.send(corpoBatch(gets));});
}
async function usuario(){  // simula: filtrar a WO e abrir 'Mensagens'
  await batch(["AlertaSet?$skip=0&$top=20&$filter=Wo%20eq%20'88412%7C126579742'&$inlinecount=allpages"]);
  await batchXHR(["MensagemSet?$filter=ConsolidadoNum%20eq%20'105012-20260916-mEeF%2BOw%2FDh'"]);
}
setInterval(usuario, 2000);
</script></body></html>"""

ESTADO = {"expirada": False}


def consulta(url):
    caminho, _, q = url.partition("?")
    m = re.search(r"'((?:[^']|'')*)'", unquote(q))
    valor = m.group(1).replace("''", "'") if m else ""
    if caminho.endswith("AlertaSet"):
        return {"d": {"results": ALERTAS.get(valor, [])}}
    if caminho.endswith("MensagemSet"):
        return {"d": {"results": MSGS.get(valor, [])}}
    return None

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _envia(self, cod, tipo, corpo):
        b = corpo.encode(); self.send_response(cod); self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path.startswith("/site"):
            return self._envia(200, "text/html", PAGINA)
        if self.path.startswith("/srv/") and ESTADO["expirada"]:
            self.send_response(302); self.send_header("Location", "https://ipc.claro.com.br/idp/SSO.saml2")
            self.end_headers(); return
        if self.path.startswith("/srv/"):
            r = consulta(self.path[len("/srv/"):])
            return self._envia(200, "application/json", json.dumps(r)) if r else self._envia(404, "text/plain", "")
        self._envia(404, "text/plain", "")
    def do_POST(self):
        corpo = self.rfile.read(int(self.headers["Content-Length"])).decode()
        partes = "".join(
            "--x\r\nContent-Type: application/http\r\n\r\nHTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n"
            + json.dumps(consulta(g)) + "\r\n" for g in re.findall(r"^GET (\S+) HTTP", corpo, re.M)) + "--x--"
        self._envia(202, "multipart/mixed; boundary=x", partes)

def iniciar(porta):
    srv = ThreadingHTTPServer(("127.0.0.1", porta), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
