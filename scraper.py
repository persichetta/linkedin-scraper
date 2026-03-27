import asyncio
import json
import os
import re
from datetime import datetime
from playwright.async_api import async_playwright
import gspread
from google.oauth2.service_account import Credentials

# ─── CONFIGURAÇÃO ────────────────────────────────────────────────────────────
# Adicione aqui os IDs das empresas que quer monitorar.
# Para achar o ID: vá ao perfil da empresa no LinkedIn e olhe a URL
# Ex: linkedin.com/company/imaflora → pegue o slug e rode get_company_id()
EMPRESAS = [
    {"nome": "Imaflora",      "linkedin_id": "164025"},
    {"nome": "WWF Brasil",    "linkedin_id": "3326874"},
    {"nome": "ICLEI",         "linkedin_id": "3232"},
    # Adicione mais empresas aqui
]

PALAVRAS_CHAVE = ""          # Filtra por cargo (ex: "marketing", "dados"). Deixe "" para todas.
GOOGLE_SHEET_ID = os.environ["GOOGLE_SHEET_ID"]   # Vem do secret do GitHub
# ─────────────────────────────────────────────────────────────────────────────


async def login(page):
    """Faz login no LinkedIn com as credenciais dos secrets do GitHub."""
    print("🔐 Fazendo login no LinkedIn...")
    await page.goto("https://www.linkedin.com/login")
    await page.fill("#username", os.environ["LINKEDIN_EMAIL"])
    await page.fill("#password", os.environ["LINKEDIN_PASSWORD"])
    await page.click('[type="submit"]')
    await page.wait_for_load_state("networkidle")

    # Verifica se caiu em verificação extra (CAPTCHA, e-mail, etc.)
    if "checkpoint" in page.url or "challenge" in page.url:
        raise Exception("⚠️ LinkedIn pediu verificação extra. Faça login manual uma vez para limpar o checkpoint.")
    print("✅ Login OK")


async def coletar_vagas(page, empresa):
    """Acessa a página de vagas da empresa e extrai os dados."""
    url = (
        f"https://www.linkedin.com/jobs/search/"
        f"?f_C={empresa['linkedin_id']}"
        f"&keywords={PALAVRAS_CHAVE}"
        f"&sortBy=DD"   # Mais recentes primeiro
    )
    print(f"🔍 Coletando vagas: {empresa['nome']} → {url}")
    await page.goto(url)
    await page.wait_for_load_state("networkidle")
    await asyncio.sleep(2)  # Pequena pausa para evitar detecção

    vagas = []
    cards = await page.query_selector_all(".job-card-container")

    for card in cards:
        try:
            titulo_el  = await card.query_selector(".job-card-list__title")
            local_el   = await card.query_selector(".job-card-container__metadata-item")
            link_el    = await card.query_selector("a.job-card-container__link")

            titulo = (await titulo_el.inner_text()).strip() if titulo_el else "N/A"
            local  = (await local_el.inner_text()).strip()  if local_el  else "N/A"
            href   = await link_el.get_attribute("href")    if link_el   else ""

            # Extrai o job ID da URL para detectar duplicatas
            match  = re.search(r"/jobs/view/(\d+)", href or "")
            job_id = match.group(1) if match else ""
            link   = f"https://www.linkedin.com/jobs/view/{job_id}" if job_id else ""

            vagas.append({
                "job_id":   job_id,
                "empresa":  empresa["nome"],
                "titulo":   titulo,
                "local":    local,
                "link":     link,
                "coletado": datetime.now().strftime("%Y-%m-%d"),
            })
        except Exception as e:
            print(f"  ⚠️ Erro ao processar card: {e}")

    print(f"  → {len(vagas)} vagas encontradas")
    return vagas


def salvar_no_sheets(vagas_novas, vagas_encerradas):
    """Atualiza a planilha: adiciona novas vagas e marca encerradas."""
    print("📊 Conectando ao Google Sheets...")
    creds_json = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(
        creds_json,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(GOOGLE_SHEET_ID)

    # ── Aba "Vagas" ──────────────────────────────────────────────────────────
    try:
        aba = sh.worksheet("Vagas")
    except gspread.WorksheetNotFound:
        aba = sh.add_worksheet("Vagas", rows=1000, cols=10)
        aba.append_row(["job_id","empresa","titulo","local","link","status","data_coleta","data_encerramento"])

    registros_existentes = aba.get_all_records()
    ids_existentes = {r["job_id"]: i + 2 for i, r in enumerate(registros_existentes)}  # linha real na sheet

    hoje = datetime.now().strftime("%Y-%m-%d")

    # Adiciona vagas novas
    adicionadas = 0
    for v in vagas_novas:
        if v["job_id"] and v["job_id"] not in ids_existentes:
            aba.append_row([
                v["job_id"], v["empresa"], v["titulo"], v["local"],
                v["link"], "Ativa", v["coletado"], ""
            ])
            adicionadas += 1

    # Marca vagas encerradas
    encerradas = 0
    for job_id in vagas_encerradas:
        if job_id in ids_existentes:
            linha = ids_existentes[job_id]
            aba.update_cell(linha, 6, "Encerrada")   # coluna "status"
            aba.update_cell(linha, 8, hoje)           # coluna "data_encerramento"
            encerradas += 1

    print(f"✅ Sheets atualizado: +{adicionadas} novas | {encerradas} encerradas")

    # ── Aba "Log" ────────────────────────────────────────────────────────────
    try:
        log = sh.worksheet("Log")
    except gspread.WorksheetNotFound:
        log = sh.add_worksheet("Log", rows=500, cols=5)
        log.append_row(["data","novas","encerradas","total_empresas","status"])

    log.append_row([hoje, adicionadas, encerradas, len(EMPRESAS), "OK"])


async def main():
    todas_vagas = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        await login(page)

        for empresa in EMPRESAS:
            vagas = await coletar_vagas(page, empresa)
            todas_vagas.extend(vagas)
            await asyncio.sleep(3)   # Pausa entre empresas para não ser bloqueado

        await browser.close()

    # Detecta vagas encerradas: estavam ativas na última coleta mas não apareceram agora
    ids_coletados_agora = {v["job_id"] for v in todas_vagas if v["job_id"]}

    # Lê IDs ativos do Sheets para comparar
    creds_json = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(
        creds_json, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(GOOGLE_SHEET_ID)
    try:
        aba = sh.worksheet("Vagas")
        ativos_anteriores = {
            r["job_id"] for r in aba.get_all_records()
            if r.get("status") == "Ativa" and r.get("job_id")
        }
        vagas_encerradas = ativos_anteriores - ids_coletados_agora
    except gspread.WorksheetNotFound:
        vagas_encerradas = set()

    salvar_no_sheets(todas_vagas, vagas_encerradas)
    print("🎉 Coleta finalizada!")


if __name__ == "__main__":
    asyncio.run(main())
