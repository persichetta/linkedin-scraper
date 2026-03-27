import asyncio
import json
import os
import re
from datetime import datetime
from playwright.async_api import async_playwright
import gspread
from google.oauth2.service_account import Credentials

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
    campos_permitidos = {"name", "value", "domain", "path", "expires", "httpOnly", "secure"}
    return [{k: v for k, v in c.items() if k in campos_permitidos} for c in cookies_list]


async def coletar_vagas(page, empresa):
    url = f"https://www.linkedin.com/jobs/search/?f_C={empresa['linkedin_id']}&keywords={PALAVRAS_CHAVE}&sortBy=DD"
    print(f"🔍 Navegando para: {empresa['nome']}")
    await page.goto(url)
    try:
        await page.wait_for_selector(".job-card-container", timeout=12000)
    except:
        if "checkpoint" in page.url or "login" in page.url:
            print(f"❌ Cookies expirados ou bloqueio em {empresa['nome']}")
            return []
        print(f"ℹ️ Nenhuma vaga visível para {empresa['nome']}.")
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
                    "job_id":   job_id,
                    "empresa":  empresa["nome"],
                    "titulo":   titulo,
                    "link":     f"https://www.linkedin.com/jobs/view/{job_id}",
                    "coletado": datetime.now().strftime("%Y-%m-%d"),
                    "local":    "LinkedIn"
                })
        except:
            continue
    print(f"  → {len(vagas)} vagas encontradas.")
    return vagas


def salvar_no_sheets(vagas_novas):
    if not vagas_novas:
        print("ℹ️ Nenhuma vaga nova para salvar.")
        return
    print("📊 Enviando dados para o Google Sheets...")
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from
