import asyncio
import json
import os
import re
from datetime import datetime
from playwright.async_api import async_playwright
import gspread
from google.oauth2.service_account import Credentials

EMPRESAS = [
    {"nome": "Greenpeace Brasil",                      "linkedin_id": "24917698"},
    {"nome": "Greenpeace Internacional",               "linkedin_id": "5273449"},
    {"nome": "WRI Brasil",                             "linkedin_id": "54340470"},
    {"nome": "Imaflora",                               "linkedin_id": "51715656"},
    {"nome": "ActionAid",                              "linkedin_id": "5654"},
    {"nome": "WWF Brasil",                             "linkedin_id": "25057196"},
    {"nome": "IPE Instituto de Pesquisas Ecologicas",  "linkedin_id": "28304"},
    {"nome": "Institute for Climate and Society",      "linkedin_id": "14609016"},
    {"nome": "Instituto Socioambiental",               "linkedin_id": "1208136"},
    {"nome": "Climate Ventures",                       "linkedin_id": "1220751"},
    {"nome": "WITNESS",                                "linkedin_id": "77252609"},
    {"nome": "Anistia Internacional",                  "linkedin_id": "26481"},
]

PALAVRAS_CHAVE = ""
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")


async def login(page):
    print("🔐 Fazendo login no LinkedIn...")
    await page.goto("https://www.linkedin.com/login")
    await page.wait_for_load_state("networkidle")
    await page.fill("#username", os.environ["LINKEDIN_EMAIL"])
    await page.fill("#password", os.environ["LINKEDIN_PASSWORD"])
    await page.click('[type="submit"]')
    await page.wait_for_load_state("networkidle")
    await asyncio.sleep(3)
    current_url = page.url
    print(f"   URL após login: {current_url}")
    if "feed" in current_url or "mynetwork" in current_url or "jobs" in current_url:
        print("✅ Login bem sucedido!")
        return True
    if "checkpoint" in current_url or "challenge" in current_url:
        print("❌ LinkedIn pediu verificação extra.")
        return False
    if "login" in current_url:
        print("❌ Login falhou — verifique email/senha.")
        return False
    print("✅ Login OK")
    return True


async def coletar_vagas(page, empresa):
    url = f"https://www.linkedin.com/jobs/search/?f_C={empresa['linkedin_id']}&keywords={PALAVRAS_CHAVE}&sortBy=DD"
    print(f"🔍 Navegando para: {empresa['nome']}")
    await page.goto(url)
    await asyncio.sleep(2)
    print(f"   URL atual: {page.url}")
    print(f"   Título: {await page.title()}")
    try:
        await page.wait_for_selector(".job-card-container", timeout=12000)
    except:
        if "checkpoint" in page.url or "login" in page.url or "authwall" in page.url:
            print(f"   ❌ Redirecionado para login — sessão expirou")
            return []
        print(f"   ℹ️ Nenhuma vaga visível para {empresa['nome']}.")
        return []

    vagas = []
    cards = await page.query_selector_all(".job-card-container")
    print(f"   Cards encontrados: {len(cards)}")
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
                    "job_id":   job_id,
                    "empresa":  empresa["nome"],
                    "titulo":   titulo,
                    "link":     f"https://www.linkedin.com/jobs/view/{job_id}",
                    "coletado": datetime.now().strftime("%Y-%m-%d"),
                    "local":    "LinkedIn"
                })
        except:
            continue
    print(f"   → {len(vagas)} vagas encontradas.")
    return vagas


def salvar_no_sheets(vagas_novas):
    if not vagas_novas:
        print("ℹ️ Nenhuma vaga nova para salvar.")
        return
    print("📊 Enviando dados para o Google Sheets...")
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(
        creds_info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(GOOGLE_SHEET_ID)
    try:
        aba = sh.worksheet("Vagas")
    except:
        aba = sh.add_worksheet("Vagas", rows=1000, cols=8)
        aba.append_row(["job_id", "empresa", "titulo", "local", "link", "status", "data_coleta", "data_encerramento"])
    existentes = {str(r["job_id"]) for r in aba.get_all_records()}
    adicionadas = 0
    for v in vagas_novas:
        if v["job_id"] not in existentes:
            aba.append_row([v["job_id"], v["empresa"], v["titulo"], v["local"], v["link"], "Ativa", v["coletado"], ""])
            adicionadas += 1
    print(f"✅ {adicionadas} vagas novas adicionadas.")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        ok = await login(page)
        if not ok:
            await browser.close()
            return

        todas_vagas = []
        for empresa in EMPRESAS:
            vagas = await coletar_vagas(page, empresa)
            todas_vagas.extend(vagas)
            await asyncio.sleep(7)

        await browser.close()
        salvar_no_sheets(todas_vagas)
        print("🎉 Processo finalizado.")


if __name__ == "__main__":
    asyncio.run(main())
