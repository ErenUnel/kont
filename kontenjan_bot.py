"""
İTÜ OBS kontenjan takip botu (GitHub Actions uyumlu)
- Belirlenen CRN'lerin kontenjanını ve yazılan sayısını dakikada bir kontrol eder
- Kontenjan artarsa veya boş yer açılırsa bildirim yollar (ntfy veya Telegram)

Gizli bilgiler ortam değişkeninden okunur:
  NTFY_KONU                          -> ntfy için
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID   -> Telegram için
"""

import os
import random
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

# ================== AYARLAR ==================
DERS_URL = "https://obs.itu.edu.tr/public/DersProgram/DersProgramSearch?programSeviyeTipiAnahtari=LS&dersBransKoduId=3"

TAKIP_EDILEN_CRNLER = ["12575"]  # Computer Vision, Computer Security

KONTROL_ARALIGI_SN = 60  # 60 sn'nin altına inme

# Kaç dakika sonra kapansın (GitHub Actions için). 0 = sonsuza kadar çalış
CALISMA_SURESI_DK = int(os.getenv("CALISMA_SURESI_DK", "0"))

NTFY_KONU = os.getenv("NTFY_KONU", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
# =============================================

HEADERS = {
    "User-Agent": "Mozilla/5.0 (kontenjan-takip, kisisel kullanim)",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://obs.itu.edu.tr/public/DersProgram",
}
TR_SAATI = timezone(timedelta(hours=3))


def bildirim_gonder(mesaj: str) -> None:
    print(f"[BİLDİRİM] {mesaj}")
    try:
        if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={"chat_id": TELEGRAM_CHAT_ID, "text": mesaj},
                timeout=15,
            )
        if NTFY_KONU:
            requests.post(
                f"https://ntfy.sh/{NTFY_KONU}",
                data=mesaj.encode("utf-8"),
                headers={"Priority": "urgent", "Tags": "rotating_light"},
                timeout=15,
            )
    except requests.RequestException as e:
        print(f"[!] Bildirim gönderilemedi: {e}")


def kontenjanlari_cek() -> dict:
    """{crn: (ders_adi, kontenjan, yazilan)} döndürür."""
    r = requests.get(DERS_URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    sonuc = {}
    for satir in soup.find_all("tr"):
        hucreler = [td.get_text(" ", strip=True) for td in satir.find_all("td")]
        if len(hucreler) < 11:
            continue
        crn = hucreler[0]
        if crn in TAKIP_EDILEN_CRNLER:
            # Sütunlar: 0 CRN, 1 Kod, 2 Ad, ..., 9 Kontenjan, 10 Yazılan
            try:
                sonuc[crn] = (hucreler[2], int(hucreler[9]), int(hucreler[10]))
            except ValueError:
                pass
    return sonuc


def main() -> None:
    if not (NTFY_KONU or (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)):
        print("[!] Bildirim ayarı yok: NTFY_KONU veya TELEGRAM_* tanımla.")

    bitis = time.time() + CALISMA_SURESI_DK * 60 if CALISMA_SURESI_DK else None
    if not bitis:
        bildirim_gonder("✅ Kontenjan botu başladı: " + ", ".join(TAKIP_EDILEN_CRNLER))

    onceki = {}
    ardisik_hata = 0

    while bitis is None or time.time() < bitis:
        zaman = datetime.now(TR_SAATI).strftime("%H:%M:%S")
        try:
            veri = kontenjanlari_cek()
            ardisik_hata = 0

            if not veri:
                print(f"[{zaman}] Uyarı: CRN'ler sayfada bulunamadı.")

            for crn, (ad, kont, yaz) in veri.items():
                print(f"[{zaman}] {crn} {ad}: {yaz}/{kont}", flush=True)
                eski = onceki.get(crn)

                if eski:
                    _, eski_kont, eski_yaz = eski
                    if kont > eski_kont:
                        bildirim_gonder(
                            f"🚨 KONTENJAN ARTTI!\n{crn} {ad}\n"
                            f"{eski_kont} → {kont} (yazılan: {yaz})"
                        )
                    elif yaz < kont and not (eski_yaz < eski_kont):
                        bildirim_gonder(f"🟢 BOŞ YER AÇILDI!\n{crn} {ad}\nDurum: {yaz}/{kont}")
                elif yaz < kont:
                    bildirim_gonder(f"🟢 Şu an yer var: {crn} {ad} ({yaz}/{kont})")

                onceki[crn] = (ad, kont, yaz)

        except Exception as e:
            ardisik_hata += 1
            print(f"[{zaman}] Hata: {e}", flush=True)
            if ardisik_hata == 5:
                bildirim_gonder(f"⚠️ Bot 5 kez üst üste hata aldı: {e}")

        time.sleep(KONTROL_ARALIGI_SN + random.randint(0, 15))


if __name__ == "__main__":
    main()
