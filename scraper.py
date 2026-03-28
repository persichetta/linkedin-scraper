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

GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")


def normalizar_cookies(cookies_list):
    campos_permitidos = {"name", "value", "domain", "path", "expires", "httpOnly", "secure"}
    return [{k: v for k, v in c.items() if k in campos_permitidos} for c in cookies_list]


async def coletar_vagas(page, empresa):
    # Usa URL da página de vagas da empresa (diferente da busca)
    url = f"https://www.linkedin.com/company/{empresa['linkedin_id']}/jobs/"
    print(f"🔍 Navegando para: {empresa['nome']}")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        print(f"   ❌ Erro ao navegar: {e}")
        return []
    await asyncio.sleep(3)
    print(f"   URL atual: {page.url}")
    print(f"   Título: {await page.title()}")

    try:
        await page.wait_for_selector(".job-card-container", timeout=10000)
    except:
        if "checkpoint" in page.url or "login" in page.url or "authwall" in page.url:
            print(f"   ❌ Redirecionado para login/checkpoint")
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
    try:
        cookies_raw = os.environ.get("LINKEDIN_COOKIES")
        if not cookies_raw:
            raise ValueError("Secret LINKEDIN_COOKIES não encontrada!")
        cookies_list = normalizar_cookies(json.loads(cookies_raw))
        print(f"✅ {len(cookies_list)} cookies carregados.")
    except Exception as e:
        print(f"❌ Erro ao carregar cookies: {e}")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        await context.add_cookies(cookies_list)
        page = await context.new_page()

        print("🔐 Verificando autenticação...")
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        print(f"   URL após feed: {page.url}")
        if "login" in page.url or "authwall" in page.url:
            print("❌ Cookies inválidos.")
            await browser.close()
            return
        print("✅ Autenticado com sucesso!")

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
