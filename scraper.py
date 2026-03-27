import asyncio
import json
import os
import re
import random
from datetime import datetime
from playwright.async_api import async_playwright
import gspread
from google.oauth2.service_account import Credentials

# --- LISTA MANTIDA INTEGRALMENTE ---
EMPRESAS = [
    {"nome": "WRI Brasil",               "linkedin_id": "54340470"},
    {"nome": "Imaflora",                 "linkedin_id": "51715656"},
    {"nome": "ActionAid",                "linkedin_id": "5654"},
    {"nome": "WWF Brasil",               "linkedin_id": "25057196"},
    {"nome": "IPE Instituto de Pesquisas Ecologicas", "linkedin_id": "28304"},
    {"nome": "Institute for Climate and Society", "linkedin_id": "14609016"},
    {"nome": "Instituto Socioambiental", "linkedin_id": "1208136"},
    {"nome": "Climate Ventures",         "linkedin_id": "1220751"},
    {"nome": "WITNESS",                  "linkedin_id": "77252609"},
    {"nome": "Anistia Internacional",    "linkedin_id": "26481"},
]

PALAVRAS_CHAVE = ""
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")

async def coletar_vagas(page, empresa):
    url = f"https://www.linkedin.com/jobs/search/?f_C={empresa['linkedin_id']}&keywords={PALAVRAS_CHAVE}&sortBy=DD"
    print(f"🔍 [SCANNING] {empresa['nome']}")
    
    try:
        # Aumentar timeout para redes lentas no GitHub Actions
        await page.goto(url, wait_until="load", timeout=60000)
        await asyncio.sleep(random.uniform(3, 5))

        # --- CORREÇÃO ANTI-BLOQUEIO: SCROLL HUMANIZADO ---
        # Rola a página em pequenos passos para simular leitura
        for _ in range(3):
            await page.mouse.wheel(0, 600)
            await asyncio.sleep(random.uniform(1, 2))

        # Espera o container de vagas aparecer
        await page.wait_for_selector(".job-card-container", timeout=15000)
        cards = await page.query_selector_all(".job-card-container")
    except:
        # Se cair aqui, pode ser bloqueio ou realmente zero vagas
        if "checkpoint" in page.url:
            print(f"❌ SESSÃO BLOQUEADA em {empresa['nome']}. Verifique os cookies.")
        else:
            print(f"ℹ️ Nenhuma vaga carregada para {empresa['nome']}.")
        return []

    vagas = []
    for card in cards:
        try:
            titulo_el = await card.query_selector(".job-card-list__title")
            link_el   = await card.query_selector("a.job-card-container__link")
            
            titulo = (await titulo_el.inner_text()).strip() if titulo_el else "N/A"
            href   = await link_el.get_attribute("href") if link_el else ""
            
            job_id_match = re.search(r"/jobs/view/(\d+)", href or "")
            job_id = job_id_match.group(1) if job_id_match else None

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
    
    print(f"  → {len(vagas)} vagas validadas.")
    return vagas

def salvar_no_sheets(vagas_novas):
    if not vagas_novas: return
    
    print("📊 Persistindo dados no Google Sheets...")
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(GOOGLE_SHEET_ID)
    
    try:
        aba = sh.worksheet("Vagas")
    except:
        aba = sh.add_worksheet("Vagas", rows=2000, cols=8)
        aba.append_row(["job_id", "empresa", "titulo", "local", "link", "status", "data_coleta", "data_encerramento"])

    existentes = {str(r["job_id"]) for r in aba.get_all_records() if "job_id" in r}
    
    novas_linhas = []
    for v in vagas_novas:
        if str(v["job_id"]) not in existentes:
            novas_linhas.append([v["job_id"], v["empresa"], v["titulo"], v["local"], v["link"], "Ativa", v["coletado"], ""])
    
    if novas_linhas:
        aba.append_rows(novas_linhas)
        print(f"✅ {len(novas_linhas)} novas vagas adicionadas.")

async def main():
    # Carregar cookies do secret
    cookies_raw = os.environ.get("LINKEDIN_COOKIES")
    cookies_list = json.loads(cookies_raw)
    
    async with async_playwright() as p:
        # Configurações de Browser para evitar detecção de Bot
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0"
        )
        await context.add_cookies(cookies_list)
        page = await context.new_page()
        
        todas_vagas = []
        for empresa in EMPRESAS:
            vagas = await coletar_vagas(page, empresa)
            todas_vagas.extend(vagas)
            # Delay aleatório maior entre empresas (8 a 15 segundos)
            await asyncio.sleep(random.uniform(8, 15))
            
        await browser.close()
        salvar_no_sheets(todas_vagas)
        print("🎉 Processo finalizado com sucesso.")

if __name__ == "__main__":
    asyncio.run(main())
