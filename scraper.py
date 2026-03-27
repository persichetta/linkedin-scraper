import asyncio
import json
import os
import re
import random
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
    mapping_same_site = {
        "no_restriction": "None", "unspecified": "None", "none": "None",
        "lax": "Lax", "strict": "Strict",
    }
    campos_permitidos = {"name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite"}
    resultado = []
    for cookie in cookies_list:
        if "sameSite" in cookie and cookie["sameSite"] is not None:
            val = str(cookie["sameSite"]).lower()
            cookie["sameSite"] = mapping_same_site.get(val, "None")
        else:
            cookie["sameSite"] = "None"
        cookie_limpo = {k: v for k, v in cookie.items() if k in campos_permitidos}
        resultado.append(cookie_limpo)
    return resultado

async def coletar_vagas(page, empresa):
    url = f"https://www.linkedin.com/jobs/search/?f_C={empresa['linkedin_id']}&keywords={PALAVRAS_CHAVE}&sortBy=DD"
    print(f"🔍 Navegando para: {empresa['nome']}")
    
    await page.goto(url)
    await page.wait_for_load_state("networkidle")

    try:
        # Espera o container principal de vagas carregar
        await page.wait_for_selector(".jobs-search-results-list", timeout=15000)
        
        # --- AUTO-SCROLL PARA CARREGAR VAGAS ESCONDIDAS ---
        # O LinkedIn carrega cards conforme você rola. Vamos rolar o container da lista.
        await page.evaluate("""
            async () => {
                const container = document.querySelector('.jobs-search-results-list');
                if (container) {
                    for (let i = 0; i < 4; i++) {
                        container.scrollTop += 500;
                        await new Promise(r => setTimeout(r, 800));
                    }
                }
            }
        """)
        await asyncio.sleep(2)
    except:
        if "checkpoint" in page.url or "login" in page.url:
            print(f"❌ Bloqueio detectado em {empresa['nome']}. Atualize os cookies.")
            return []
        print(f"ℹ️ Nenhuma estrutura de vagas encontrada para {empresa['nome']}. Pode estar sem vagas.")
        return []

    vagas = []
    cards = await page.query_selector_all(".job-card-container")
    
    for card in cards:
        try:
            # Garante que o card está visível para pegar o texto
            await card.scroll_into_view_if_needed()
            
            titulo_el = await card.query_selector(".job-card-list__title")
            link_el   = await card.query_selector("a.job-card-container__link")
            
            if titulo_el and link_el:
                titulo = (await titulo_el.inner_text()).strip()
                href   = await link_el.get_attribute("href")
                
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
        except Exception as e:
            continue
            
    print(f"  → {len(vagas)} vagas encontradas.")
    return vagas

def salvar_no_sheets(vagas_novas):
    if not vagas_novas:
        print("ℹ️ Nenhuma vaga nova para salvar.")
        return
    
    print("📊 Enviando dados para o Google Sheets...")
    try:
        creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
        creds = Credentials.from_service_account_info(
            creds_info, scopes=["
