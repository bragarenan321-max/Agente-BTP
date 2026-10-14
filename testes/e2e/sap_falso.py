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
const PARAM = new URLSearchParams(location.search);
const CAP = !!PARAM.get('cap');
if (CAP) window.__SAP_REQUIRE = { toUrl: (n) => '/cap/' + n };
const COMPONENTE = {
  getMetadata(){ return { getComponentName(){ return 'monitor.alertas'; } }; },
  getManifestEntry(k){ return k === 'sap.app' ? {id:'monitor.alertas', title:'Monitor de alertas'}
                            : k === 'sap.ui5' ? {models:{'':{dataSource:'mainService'}}} : null; },
  getModel(){ return PARAM.get('semfontes') ? null
                   : CAP ? {sServiceUrl:'../../odata/v4/monitor/', getMetadata(){ return {getName(){ return 'sap.ui.model.odata.v4.ODataModel'; }}; }}
                   : {sServiceUrl:'/srv', getMetadata(){ return {getName(){ return 'sap.ui.model.odata.v2.ODataModel'; }}; }}; },
};
window.sap = {ushell:{Container:{}}, ui:{require: window.__SAP_REQUIRE || {toUrl:(n)=>'/'+n},
              core:{Component:{registry:{forEach(f){ f(COMPONENTE); }}}}}};
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
if (PARAM.get('simular')) setInterval(usuario, 2000);  // usuário usando o Monitor
</script></body></html>"""

ESTADO = {"expirada": False, "sem_metadata": False}

METADATA = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx Version="1.0" xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx">
 <edmx:DataServices><Schema Namespace="ZMONITOR_SRV" xmlns="http://schemas.microsoft.com/ado/2008/09/edm">
  <EntityType Name="Alerta"><Key><PropertyRef Name="ConsolidadoNum"/></Key>
   <Property Name="Wo" Type="Edm.String"/><Property Name="ConsolidadoNum" Type="Edm.String"/>
   <Property Name="Fornecedor" Type="Edm.String"/><Property Name="Data" Type="Edm.DateTime"/>
   <Property Name="Mensagem" Type="Edm.String"/></EntityType>
  <EntityType Name="Mensagem"><Key><PropertyRef Name="Matnr"/></Key>
   <Property Name="Wo" Type="Edm.String"/><Property Name="ConsolidadoNum" Type="Edm.String"/>
   <Property Name="Matnr" Type="Edm.String"/><Property Name="MaterialText" Type="Edm.String"/>
   <Property Name="Lifnr" Type="Edm.String"/><Property Name="QtdFaltante" Type="Edm.Decimal"/>
   <Property Name="Mensagem" Type="Edm.String"/></EntityType>
  <EntityContainer Name="C" m:IsDefaultEntityContainer="true" xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">
   <EntitySet Name="AlertaSet" EntityType="ZMONITOR_SRV.Alerta"/>
   <EntitySet Name="MensagemSet" EntityType="ZMONITOR_SRV.Mensagem"/>
  </EntityContainer></Schema></edmx:DataServices></edmx:Edmx>"""


# ---- Variante CAP/OData V4: nomes diferentes, serviço relativo, mensagens só por navegação
G1, G2 = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
ALERTAS_V4 = [
    {"ID": G1, "NumeroWo": "88412|126579742", "NumConsolidado": "105012-20260916-mEeF+Ow/Dh",
     "Fornecedor": "137628", "Mensagem": "Erro durante baixa de estoque"},
    {"ID": G2, "NumeroWo": "02235|230771207", "NumConsolidado": "082026-20260806-3YeQ",
     "Fornecedor": "1000835", "Mensagem": "Erro durante baixa de estoque"},
]
ITENS_V4 = {G1: [
    {"ID": "a1", "CodMaterial": "000000000022026169", "MaterialDescricao": "CABO COAXIAL",
     "CodFornecedor": "0000137628", "QtdeFaltante": 2, "Mensagem": "Estoque indisponível"},
    {"ID": "a2", "CodMaterial": "000000000011000042", "MaterialDescricao": "CONECTOR",
     "CodFornecedor": "0000137628", "QtdeFaltante": 10, "Mensagem": "Estoque indisponível"}], G2: []}
METADATA_V4 = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx"><edmx:DataServices>
<Schema Namespace="MonitorService" xmlns="http://docs.oasis-open.org/odata/ns/edm">
 <EntityType Name="Alerta"><Key><PropertyRef Name="ID"/></Key><Property Name="ID" Type="Edm.Guid"/>
  <Property Name="NumeroWo" Type="Edm.String"/><Property Name="NumConsolidado" Type="Edm.String"/>
  <Property Name="Fornecedor" Type="Edm.String"/><Property Name="Mensagem" Type="Edm.String"/>
  <NavigationProperty Name="itens" Type="Collection(MonitorService.Item)"/></EntityType>
 <EntityType Name="Arquivado"><Key><PropertyRef Name="ID"/></Key><Property Name="ID" Type="Edm.Guid"/>
  <Property Name="NumeroWo" Type="Edm.String"/><Property Name="NumConsolidado" Type="Edm.String"/>
  <Property Name="Mensagem" Type="Edm.String"/>
  <NavigationProperty Name="itens" Type="Collection(MonitorService.Item)"/></EntityType>
 <EntityType Name="Item"><Key><PropertyRef Name="ID"/></Key><Property Name="ID" Type="Edm.String"/>
  <Property Name="alerta_ID" Type="Edm.Guid"/><Property Name="CodMaterial" Type="Edm.String"/>
  <Property Name="MaterialDescricao" Type="Edm.String"/><Property Name="CodFornecedor" Type="Edm.String"/>
  <Property Name="QtdeFaltante" Type="Edm.Decimal"/><Property Name="Mensagem" Type="Edm.String"/></EntityType>
 <EntityContainer Name="EntityContainer">
  <EntitySet Name="Arq" EntityType="MonitorService.Arquivado"/>
  <EntitySet Name="Alertas" EntityType="MonitorService.Alerta"/>
  <EntitySet Name="Itens" EntityType="MonitorService.Item"/>
 </EntityContainer></Schema></edmx:DataServices></edmx:Edmx>"""


def consulta_v4(caminho):
    """Responde (status, corpo) para /cap/odata/v4/monitor/..."""
    if caminho.startswith("$metadata"):
        return 200, METADATA_V4
    if caminho.startswith("Arq"):
        return 400, json.dumps({"error": {"code": "400", "message": "Conjunto desativado"}})
    m = re.match(r"Alertas\(([0-9a-f-]+)\)/itens", caminho)
    if m:
        return 200, json.dumps({"value": ITENS_V4.get(m.group(1), [])})
    if caminho.startswith("Alertas?"):
        q = unquote(caminho.split("?", 1)[1])
        v = re.search(r"NumeroWo eq '((?:[^']|'')*)'", q)
        valor = v.group(1).replace("''", "'") if v else ""
        return 200, json.dumps({"value": [a for a in ALERTAS_V4 if a["NumeroWo"] == valor]})
    return 404, "{}"


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
        if self.path.startswith("/cap/odata/v4/monitor/"):
            cod, corpo = consulta_v4(self.path[len("/cap/odata/v4/monitor/"):])
            tipo = "application/xml" if corpo.startswith("<?xml") else "application/json"
            return self._envia(cod, tipo, corpo)
        if self.path.startswith("/srv/$metadata"):
            if ESTADO["sem_metadata"]:
                return self._envia(403, "application/json", '{"error":{"message":"sem acesso"}}')
            return self._envia(200, "application/xml", METADATA)
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
