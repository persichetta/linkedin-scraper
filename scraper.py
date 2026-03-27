import asyncio
import json
import os
import re
import random
from datetime import datetime
from playwright.async_api import async_playwright
import gspread
from google.oauth2.service_account import Credentials

# --- CONFIGURAÇÃO ---
EMPRESAS = [
    {"nome": "Greenpeace Brasil",        "linkedin_id": "24917698"},
    {"nome": "Greenpeace Internacional", "linkedin_id": "5273449"},
    {"nome": "WRI Brasil",               "linkedin_id": "54340470"},
    {"nome": "Imaflora",                 "linkedin_id": "51715656"},
    {"nome": "ActionAid",                "linkedin_id": "5654"},
]

PALAVRAS_CHAVE = ""
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")

def normalizar_cookies(cookies_list):
    mapping_same_site = {"no_restriction": "None", "unspecified": "None", "none": "None", "lax": "Lax", "strict": "Strict"}
    campos_permitidos = {"name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite"}
    resultado = []
    for cookie in cookies_list:
        if "sameSite" in cookie and cookie["sameSite"] is not None:
            val = str(cookie["sameSite"]).lower()
            cookie["sameSite"] = mapping_same_site.get(val, "None")
        else:
            cookie["sameSite"] = "None"
        resultado.append({k: v for k, v in cookie.items() if k in campos_permitidos})
    return resultado

async def coletar_vagas(page, empresa):
    url = f"https://www.linkedin.com/jobs/search/?f_C={empresa['linkedin_id']}&keywords={PALAVRAS_CHAVE}&sortBy=DD"
    print(f"🔍 Navegando para: {empresa['nome']}")
    
    try:
        await page.goto(url, wait_until="networkidle", timeout=60000)
        # Espera o container de resultados
        await page.wait_for_selector(".jobs-search-results-list", timeout=20000)
        
        # Scroll dinâmico para carregar lazy loading
        for _ in range(5):
            await page.evaluate("document.querySelector('.jobs-search-results-list').scrollTop += 1000")
            await asyncio.sleep(1.5)
            
    except Exception as e:
        if "checkpoint" in page.url:
            print(f"❌ Bloqueio em {empresa['nome']}. Verifique os cookies.")
        else:
            print(f"ℹ️ Sem vagas ou erro de carregamento para {empresa['nome']}.")
        return []

    vagas = []
    cards = await page.query_selector_all(".job-card-container")
    
    for card in cards:
        try:
            titulo_el = await card.query_selector(".job-card-list__title")
            link_el = await card.query_selector("a.job-card-container__link")
            
            if titulo_el and link_el:
                titulo = (await titulo_el.inner_text()).strip()
                href = await link_el.get_attribute("href")
                job_id = re.search(r"/jobs/view/(\d+)", href).group(1) if re.search(r"/jobs/view/(\d+)", href) else None
                
                if job_id:
                    vagas.append({
                        "job_id": job_id,
                        "empresa": empresa["nome"],
                        "titulo": titulo,
                        "link": f"https://www.linkedin.com/jobs/view/{job_id}",
                        "coletado": datetime.now().strftime("%Y-%m-%d"),
                        "status": "Ativa"
                    })
        except: continue
            
    print(f"  → {len(vagas)} vagas encontradas.")
    return vagas

def atualizar_planilha(vagas_coletadas):
    print("📊 Sincronizando com Google Sheets...")
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(GOOGLE_SHEET_ID)
    
    try:
        aba = sh.worksheet("Vagas")
    except:
        aba = sh.add_worksheet("Vagas", rows=2000, cols=8)
        aba.append_row(["job_id", "empresa", "titulo", "link", "status", "data_coleta", "data_encerramento"])

    dados_atuais = aba.get_all_records()
    # Mapeia ID para a linha na planilha (index + 2 porque tem cabeçalho e gspread é 1-indexed)
    mapeamento_ids = {str(r["job_id"]): idx + 2 for idx, r in enumerate(dados_atuais)}
    ids_vistos_agora = {v["job_id"] for v in vagas_coletadas}
    hoje = datetime.now().strftime("%Y-%m-%d")

    # 1. Adicionar Novas ou Ignorar Existentes
    novas_vagas = []
    for v in vagas_coletadas:
        if v["job_id"] not in mapeamento_ids:
            novas_vagas.append([v["job_id"], v["empresa"], v["titulo"], v["link"], "Ativa", v["coletado"], ""])
    
    if novas_vagas:
        aba.append_rows(novas_vagas)
        print(f"✅ {len(novas_vagas)} novas vagas adicionadas.")

    # 2. Marcar Encerradas (estavam "Ativa" mas não vieram na coleta)
    vagas_para_encerrar = []
    for r in dados_atuais:
        if r["status"] == "Ativa" and str(r["job_id"]) not in ids_vistos_agora:
            linha = mapeamento_ids[str(r["job_id"])]
            # Colunas F (status) e H (data_encerramento)
            vagas_para_encerrar.append({'range': f'E{linha}:G{linha}', 'values': [['Encerrada', r['data_coleta'], hoje]]})

    if vagas_para_encerrar:
        aba.batch_update([
            {'range': f'E{mapeamento_ids[str(r["job_id"])]}', 'values': [['Encerrada']]} for r in dados_atuais 
            if r["status"] == "Ativa" and str(r["job_id"]) not in ids_vistos_agora
        ])
        # Nota: Simplificado para update individual por segurança de índice
        for r in dados_atuais:
             if r["status"] == "Ativa" and str(r["job_id"]) not in ids_vistos_agora:
                 linha = mapeamento_ids[str(r["job_id"])]
                 aba.update_cell(linha, 5, "Encerrada")
                 aba.update_cell(linha, 7, hoje)
        print("🚩 Vagas antigas marcadas como encerradas.")

async def main():
    cookies_raw = os.environ.get("LINKEDIN_COOKIES")
    cookies_list = normalizar_cookies(json.loads(cookies_raw))

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0")
        await context.add_cookies(cookies_list)
        page = await context.new_page()
        
        todas_vagas = []
        for empresa in EMPRESAS:
            vagas = await coletar_vagas(page, empresa)
            todas_vagas.extend(vagas)
            await asyncio.sleep(random.uniform(10, 20)) # Delay humano
            
        await browser.close()
        atualizar_planilha(todas_vagas)
        print("🎉 Pipeline finalizado.")

if __name__ == "__main__":
    asyncio.run(main())
