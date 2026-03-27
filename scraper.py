import asyncio
import json
import os
import re
from datetime import datetime
from playwright.async_api import async_playwright
import gspread
from google.oauth2.service_account import Credentials

# ─── CONFIGURAÇÃO ────────────────────────────────────────────────────────────
EMPRESAS = [
    {"nome": "Greenpeace Brasil",        "linkedin_id": "24917698"},
    {"nome": "Greenpeace Internacional", "linkedin_id": "5273449"},
    {"nome": "WRI Brasil",               "linkedin_id": "54340470"},
    {"nome": "Imaflora",                  "linkedin_id": "51715656"},
    {"nome": "ActionAid",                "linkedin_id": "5654"},
]

PALAVRAS_CHAVE = ""  
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")

async def coletar_vagas(page, empresa):
    url = f"https://www.linkedin.com/jobs/search/?f_C={empresa['linkedin_id']}&keywords={PALAVRAS_CHAVE}&sortBy=DD"
    print(f"🔍 Navegando para: {empresa['nome']}")
    
    await page.goto(url)
    try:
        # Espera carregar ou detecta se fomos barrados
        await page.wait_for_selector(".job-card-container", timeout=12000)
    except:
        if "checkpoint" in page.url or "login" in page.url:
             print(f"❌ Erro: Cookies expirados ou bloqueio em {empresa['nome']}")
             return []
        print(f"ℹ️ Nenhuma vaga visível para {empresa['nome']} no momento.")
        return []

    vagas = []
    cards = await page.query_selector_all(".job-card-container")

    for card in cards:
        try:
            titulo_el = await card.query_selector(".job-card-list__title")
            link_el   = await card.query_selector("a.job-card-container__link")
            
            titulo = (await titulo_el.inner_text()).strip() if titulo_el else "N/A"
            href   = await link_el.get_attribute("href") if link_el else ""
            match  = re.search(r"/jobs/view/(\d+)", href or "")
            job_id = match.group(1) if match else ""

            if job_id:
                vagas.append({
                    "job_id": job_id,
                    "empresa": empresa["nome"],
                    "titulo": titulo,
                    "link": f"https://www.linkedin.com/jobs/view/{job_id}",
                    "coletado": datetime.now().strftime("%Y-%m-%d"),
                    "local": "LinkedIn"
                })
        except: continue

    print(f"  → {len(vagas)} vagas encontradas.")
    return vagas

def salvar_no_sheets(vagas_novas):
    if not vagas_novas: return
    
    print("📊 Enviando dados para o Google Sheets...")
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(GOOGLE_SHEET_ID)

    try:
        aba = sh.worksheet("Vagas")
    except:
        aba = sh.add_worksheet("Vagas", rows=1000, cols=8)
        aba.append_row(["job_id","empresa","titulo","local","link","status","data_coleta","data_encerramento"])

    # Evita duplicatas básicas comparando com os últimos IDs na planilha
    existentes = [r["job_id"] for r in aba.get_all_records()[-100:]] # Pega os últimos 100
    
    for v in vagas_novas:
        if v["job_id"] not in existentes:
            aba.append_row([v["job_id"], v["empresa"], v["titulo"], v["local"], v["link"], "Ativa", v["coletado"], ""])

async def main():
    # Carrega os cookies da variável de ambiente (GitHub Secret)
    try:
        cookies_raw = os.environ.get("LINKEDIN_COOKIES")
        if not cookies_raw:
            raise ValueError("Secret LINKEDIN_COOKIES não encontrada!")
        cookies_list = json.loads(cookies_raw)
    except Exception as e:
        print(f"❌ Erro ao carregar cookies: {e}")
        return

    async with async_playwright() as p:
        # Launch com argumentos para rodar bem no GitHub Actions
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        
        # Injeção dos cookies do Secret
        await context.add_cookies(cookies_list)
        
        page = await context.new_page()
        todas_vagas = []

        for empresa in EMPRESAS:
            vagas = await coletar_vagas(page, empresa)
            todas_vagas.extend(vagas)
            await asyncio.sleep(7) # Delay maior para segurança

        await browser.close()
        salvar_no_sheets(todas_vagas)
        print("🎉 Processo finalizado.")

if __name__ == "__main__":
    asyncio.run(main())
