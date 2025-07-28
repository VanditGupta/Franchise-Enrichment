import asyncio
import json
import os
import re
import tempfile
from typing import List, Union

import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel
from stagehand import Stagehand, StagehandConfig
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from google.cloud import storage

# ─────────────── Load ENV ───────────────
load_dotenv()

PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
MODEL_API_KEY = os.getenv("MODEL_API_KEY")

pplx_client = OpenAI(api_key=PERPLEXITY_API_KEY, base_url="https://api.perplexity.ai")

FALLBACK_CONTACT = {
    "corporate_phone": "(972) 831-0911",
    "corporate_email": "gfcinfo@goldenchick.com",
    "linkedin_url": "",
    "url Sources": ["https://www.goldenchick.com/contact-us"],
}


class CompanyInfo(BaseModel):
    corporate_name: str
    registered_address: str
    owner_name: Union[str, List[dict]]
    source_url: str


def enrich_contact_info(franchise_name: str, address: str, owner_name: str) -> dict:
    prompt = f"""
You are an AI assistant tasked with finding real-time contact information using the web.

1. Search for business contact info (email, phone) for this specific franchise location:
   • Franchise: {franchise_name}
   • Address: {address}

2. If unavailable, search for contact info for the owner:
   • Owner: {owner_name}

3. Find the **owner’s personal LinkedIn profile and make sure that owner's LinkedIn URL actually has the franchise name associated with it** (URL must contain “/in/”, not “/company/”).

Return JSON only:
{{"corporate_phone":"...", "corporate_email":"...", "linkedin_url":"...", "url Sources":["https://..."]}}
"""
    try:
        resp = pplx_client.chat.completions.create(
            model="sonar-pro",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        raw = resp.choices[0].message.content
        cleaned = re.sub(r"^```json|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        data = json.loads(cleaned or "{}")
    except Exception as e:
        print(f"Perplexity error: {e}")
        data = {}

    return {
        "corporate_phone": data.get("corporate_phone") or FALLBACK_CONTACT["corporate_phone"],
        "corporate_email": data.get("corporate_email") or FALLBACK_CONTACT["corporate_email"],
        "linkedin_url": data.get("linkedin_url") or FALLBACK_CONTACT["linkedin_url"],
        "url Sources": data.get("url Sources") or FALLBACK_CONTACT["url Sources"],
    }


async def enrich_opencorporates(franchise_name: str, state_abbr: str) -> dict:
    config = StagehandConfig(
        env="BROWSERBASE",
        model_name="openai/gpt-4.1-mini",
        model_client_options={"apiKey": MODEL_API_KEY},
    )
    stagehand = Stagehand(config)
    try:
        await stagehand.init()
        page = stagehand.page

        await page.goto("https://opencorporates.com/")
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(2)

        search_input = page.locator('input[name="q"]')
        await search_input.wait_for(state="visible", timeout=5000)
        await search_input.fill(franchise_name)
        await search_input.press("Enter")

        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(2)

        await page.act(f"Click the company link whose address contains the state abbreviation {state_abbr}")
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(2)

        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(3)

        source_url = page.url

        corporate_name = "N/A"
        try:
            h1 = await page.query_selector("h1.wrapping_heading.fn.org")
            corporate_name = await h1.inner_text() if h1 else "N/A"
        except:
            pass

        registered_address = "N/A"
        try:
            li_list = await page.query_selector_all("dd.registered_address.adr ul.address_lines li.address_line")
            address_parts = [await li.inner_text() for li in li_list]
            registered_address = ", ".join(address_parts) if address_parts else "N/A"
        except:
            pass

        owner_name = "N/A"
        try:
            agent_el = await page.query_selector("dd.agent_name")
            if agent_el:
                owner_name = await agent_el.inner_text()
        except:
            pass

        return CompanyInfo(
            corporate_name=corporate_name,
            registered_address=registered_address,
            owner_name=owner_name,
            source_url=source_url,
        ).model_dump()

    finally:
        await stagehand.close()


def download_blob(bucket_name, source_blob_name, destination_file_name):
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(source_blob_name)
    blob.download_to_filename(destination_file_name)
    print(f"✅ Downloaded {source_blob_name} to {destination_file_name}.")


def upload_blob(bucket_name, source_file_name, destination_blob_name):
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)
    blob.upload_from_filename(source_file_name)
    print(f"✅ Uploaded {source_file_name} to {destination_blob_name}.")


async def process_row(idx, row):
    franchise = str(row["Franchisee"]).strip()
    state = str(row["State"]).strip()
    enriched, contact, confidence = {}, {}, 0.0

    if not franchise or pd.isna(franchise):
        return idx, {}

    try:
        enriched = await enrich_opencorporates(franchise, state)
    except Exception as e:
        print(f"Stagehand error (Row {idx}): {e}")
        enriched = {"corporate_name": "N/A", "registered_address": "N/A", "owner_name": "N/A", "source_url": ""}

    contact = enrich_contact_info(franchise_name=franchise, address=state, owner_name=enriched["owner_name"])

    stagehand_vals = [enriched["corporate_name"], enriched["registered_address"], enriched["owner_name"]]
    contact_vals = [contact["corporate_phone"], contact["corporate_email"], contact["linkedin_url"]]

    fallback_contact_vals = [
        FALLBACK_CONTACT["corporate_phone"],
        FALLBACK_CONTACT["corporate_email"],
        "",
    ]

    def _good(v, fallback): return v not in ("", "N/A") and v not in fallback

    good_fields = sum(_good(v, []) for v in stagehand_vals) + sum(_good(v, fallback_contact_vals) for v in contact_vals)
    confidence = round(good_fields / 6, 2)

    return idx, {
        "Corporate Name": enriched["corporate_name"],
        "Corporate Address": enriched["registered_address"],
        "Franchisee Owner": enriched["owner_name"],
        "url Sources": enriched["source_url"] + ", " + ", ".join(contact["url Sources"]),
        "Corporate Phone": contact["corporate_phone"],
        "Corporate Email": contact["corporate_email"],
        "LinkedIn": contact["linkedin_url"],
        "Confidence": confidence,
    }


async def process_excel_gcs(bucket_name: str, input_blob: str, output_blob: str):
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, "franchise_input.xlsx")
        output_path = os.path.join(tmpdir, "franchise_data_enriched.csv")

        download_blob(bucket_name, input_blob, input_path)
        df = pd.read_excel(input_path)

        tasks = [process_row(idx, row) for idx, row in df.iterrows()]
        results = await asyncio.gather(*tasks)

        for idx, updates in results:
            for col, val in updates.items():
                df.at[idx, col] = val

        df.to_csv(output_path, index=False)
        upload_blob(bucket_name, output_path, output_blob)
        print(f"\n✅ Output saved to GCS as {output_blob}")


if __name__ == "__main__":
    BUCKET_NAME = "franchise-enrichment-bucket"
    INPUT_BLOB = "input/franchise_input.xlsx"
    OUTPUT_BLOB = "output/franchise_data_enriched.csv"
    asyncio.run(process_excel_gcs(BUCKET_NAME, INPUT_BLOB, OUTPUT_BLOB))
