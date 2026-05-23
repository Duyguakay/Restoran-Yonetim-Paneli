import os
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash
import mysql.connector
from datetime import datetime, timedelta

load_dotenv(dotenv_path="pass.env")
app = Flask(__name__)
app.secret_key = "RESTORAN_ozel_anahtar"

def get_db():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD"), 
        database=os.getenv("DB_NAME", "RestoranDB")
    )

@app.route('/')
def index():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM MASA ORDER BY Masa_Id ASC")
    masalar = cursor.fetchall()
    simdi = datetime.now()
    bugun_bas = simdi.replace(hour=0, minute=0, second=0)
    bugun_bit = simdi.replace(hour=23, minute=59, second=59)
    cursor.execute("SELECT Masa_Id, Tarih FROM REZERVASYON WHERE Tarih BETWEEN %s AND %s", (bugun_bas, bugun_bit))
    gunluk_rezler = cursor.fetchall()
    for m in masalar:
        m['durum'] = 'bos'
        for r in gunluk_rezler:
            if r['Masa_Id'] == m['Masa_Id']:
                if simdi >= r['Tarih'] - timedelta(hours=1) and simdi <= r['Tarih'] + timedelta(hours=2):
                    m['durum'] = 'dolu'
                elif r['Tarih'] > simdi:
                    m['durum'] = 'rezerve'
    cursor.close()
    db.close()
    return render_template('index.html', masalar=masalar, sayfa='genel')

@app.route('/masalar')
def masalar_listesi():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM MASA ORDER BY Masa_Id ASC")
    masalar = cursor.fetchall()
    cursor.close()
    db.close()
    return render_template('masalar.html', masalar=masalar, sayfa='masa')

@app.route('/menu')
def menu_listesi():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT Urun_Id AS urun_id, Urun_Adi AS urun_adi, Fiyat AS fiyat, Aciklama AS aciklama FROM urun ORDER BY Urun_Id ASC")
    urunler = cursor.fetchall()
    cursor.close()
    db.close()
    return render_template('menu.html', urunler=urunler, sayfa='menu')

# --- SİPARİŞLER / ADİSYON LİSTESİ---
@app.route('/siparisler')
def siparis_listesi():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        # INNER JOIN yerine LEFT JOIN koyduk ki boş masalar da listede gelsin ve ürün eklenebilsin!
        query = """
            SELECT m.Masa_Id AS Masa_Id, m.Konum, s.Siparis_Id, COALESCE(s.Tutar, 0.00) AS Tutar
            FROM MASA m
            LEFT JOIN SIPARIS s ON m.Masa_Id = s.Masa_Id
            ORDER BY m.Masa_Id ASC
        """
        cursor.execute(query)
        siparisler = cursor.fetchall()
        for s in siparisler:
            s['Kalemler'] = []
            if s['Siparis_Id']:
                cursor.execute("""
                    SELECT sd.Detay_Id, sd.Adet, u.Fiyat, sd.Ara_Toplam, u.Urun_Adi 
                    FROM siparis_detay sd 
                    JOIN urun u ON sd.Urun_Id = u.Urun_Id 
                    WHERE sd.Siparis_Id = %s
                """, (s['Siparis_Id'],))
                s['Kalemler'] = cursor.fetchall()
    except Exception as e:
        siparisler = []
        flash(f"Siparişler yüklenirken hata: {str(e)}", "danger")
        
    cursor.execute("SELECT Urun_Id AS urun_id, Urun_Adi AS urun_adi, Fiyat AS fiyat FROM urun ORDER BY Urun_Adi ASC")
    urunler = cursor.fetchall()
    cursor.close()
    db.close()
    return render_template('siparisler.html', siparisler=siparisler, urunler=urunler, sayfa='siparis')

@app.route('/rezervasyon-olustur', methods=['POST'])
def rezervasyon_olustur():
    ad = request.form.get('ad')
    soyad = request.form.get('soyad')
    telefon = request.form.get('telefon')
    tarih = request.form.get('tarih')
    saat = request.form.get('saat')
    kisi_sayisi_str = request.form.get('kisi_sayisi')
    ana_masa_id = request.form.get('masa_id')

    if not all([ad, soyad, tarih, saat, kisi_sayisi_str, ana_masa_id]):
        flash("❌ Hata: Lütfen rezervasyon formundaki tüm alanları eksiksiz doldurun!", "danger")
        return redirect(url_for('index'))

    kisi_sayisi = int(kisi_sayisi_str)
    secilen_zaman_str = f"{tarih} {saat}:00"
    secilen_zaman = datetime.strptime(secilen_zaman_str, "%Y-%m-%d %H:%M:%S")
    alt_limit = secilen_zaman - timedelta(hours=2)
    ust_limit = secilen_zaman + timedelta(hours=2)

    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT Kapasite, Konum FROM MASA WHERE Masa_Id = %s", (ana_masa_id,))
        body_masa = cursor.fetchone()
        if not body_masa:
            flash(f"❌ Hata: {ana_masa_id} numaralı masa bulunamadı!", "danger")
            return redirect(url_for('index'))

        check_query = "SELECT * FROM REZERVASYON WHERE Masa_Id = %s AND Tarih BETWEEN %s AND %s"
        cursor.execute(check_query, (ana_masa_id, alt_limit, ust_limit))
        if cursor.fetchone():
            flash(f"❌ Çakışma! Seçilen Masa {ana_masa_id} o saatte zaten rezerve edilmiş.", "danger")
            return redirect(url_for('index'))

        ayrilacak_masalar = [int(ana_masa_id)]
        kalan_kisi = kisi_sayisi - body_masa['Kapasite']

        if kalan_kisi > 0:
            otomatik_masa_query = """
                SELECT Masa_Id, Kapasite,
                       (CASE WHEN Konum = %s THEN 0 ELSE 1 END) AS yakinlik_onceligi
                FROM MASA 
                WHERE Masa_Id != %s AND Masa_Id NOT IN (
                    SELECT Masa_Id FROM REZERVASYON WHERE Tarih BETWEEN %s AND %s
                )
                ORDER BY yakinlik_onceligi ASC, Masa_Id ASC
            """
            cursor.execute(otomatik_masa_query, (body_masa['Konum'], ana_masa_id, alt_limit, ust_limit))
            kullanilabilir_masalar = cursor.fetchall()

            for masa in kullanilabilir_masalar:
                if kalan_kisi <= 0:
                    break
                ayrilacak_masalar.append(masa['Masa_Id'])
                kalan_kisi -= masa['Kapasite']

            if kalan_kisi > 0:
                flash(f"❌ Hata: Restorandaki tüm uygun masalar {kisi_sayisi} kişiyi ağırlamak için yetersiz!", "danger")
                return redirect(url_for('index'))

        cursor.execute("INSERT INTO MUSTERI (Ad, Soyad, Telefon) VALUES (%s, %s, %s)", (ad, soyad, telefon))
        musteri_id = cursor.lastrowid

        for m_id in ayrilacak_masalar:
            cursor.execute(
                "INSERT INTO REZERVASYON (Tarih, Kisi_Sayisi, Musteri_ID, Masa_ID) VALUES (%s, %s, %s, %s)",
                (secilen_zaman_str, kisi_sayisi, musteri_id, m_id)
            )
        
        db.commit()
        masalar_str = ", ".join(map(str, ayrilacak_masalar))
        flash(f"✨ Otomatik Masa Atandı! {ad} {soyad} adına birbirine en yakın olan Masa ({masalar_str}) birlikte rezerve edildi.", "success")

    except Exception as e:
        db.rollback()
        flash(f"Sistem Hatası: {str(e)}", "danger")
    finally:
        cursor.close()
        db.close()
    return redirect(url_for('index'))

@app.route('/kapasite-kontrol')
def kapasite_kontrol():
    masa_id = request.args.get('masa_id')
    kisi = int(request.args.get('kisi', 0))
    tarih = request.args.get('tarih')
    saat = request.args.get('saat')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT Kapasite, Konum FROM MASA WHERE Masa_Id = %s", (masa_id,))
    masa_kontrol = cursor.fetchone()
    
    bos_masalar = []
    if masa_kontrol and kisi > masa_kontrol['Kapasite'] and tarih and saat:
        secilen_zaman_str = f"{tarih} {saat}:00"
        secilen_zaman = datetime.strptime(secilen_zaman_str, "%Y-%m-%d %H:%M:%S")
        alt_limit = secilen_zaman - timedelta(hours=2)
        ust_limit = secilen_zaman + timedelta(hours=2)
        
        query = """
            SELECT Masa_Id, Kapasite, Konum,
                   (CASE WHEN Konum = %s THEN 0 ELSE 1 END) AS yakinlik_onceligi
            FROM MASA 
            WHERE Masa_Id != %s AND Masa_Id NOT IN (
                SELECT Masa_Id FROM REZERVASYON WHERE Tarih BETWEEN %s AND %s
            )
            ORDER BY yakinlik_onceligi ASC, Masa_Id ASC
        """
        cursor.execute(query, (masa_kontrol['Konum'], masa_id, alt_limit, ust_limit))
        bos_masalar = cursor.fetchall()

    cursor.close()
    db.close()

    if masa_kontrol and kisi > masa_kontrol['Kapasite']:
        return {"uygun": False, "kapasite": masa_kontrol['Kapasite'], "bos_masalar": bos_masalar}
    return {"uygun": True}

# --- ADİSYON / SİPARİŞ ÜRÜN EKLEME ---
@app.route('/adisyon-urun-ekle/<int:masa_id>', methods=['POST'])
def adisyon_urun_ekle(masa_id):
    urun_id = request.form.get('urun_id')
    adet = int(request.form.get('adet', 1))
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT Siparis_Id FROM SIPARIS WHERE Masa_Id = %s", (masa_id,))
        siparis = cursor.fetchone()
        if not siparis:
            cursor.execute("INSERT INTO SIPARIS (Masa_Id, Tutar) VALUES (%s, 0.00)", (masa_id,))
            siparis_id = cursor.lastrowid
        else:
            siparis_id = siparis['Siparis_Id']
            
        cursor.execute("SELECT Fiyat FROM urun WHERE Urun_Id = %s", (urun_id,))
        urun = cursor.fetchone()
        if urun:
            ara_toplam = urun['Fiyat'] * adet
            cursor.execute("INSERT INTO siparis_detay (Siparis_Id, Urun_Id, Adet, Ara_Toplam) VALUES (%s, %s, %s, %s)", (siparis_id, urun_id, adet, ara_toplam))
            cursor.execute("UPDATE SIPARIS SET Tutar = Tutar + %s WHERE Siparis_Id = %s", (ara_toplam, siparis_id))
            db.commit()
            flash("✨ Ürün adisyona başarıyla eklendi.", "success")
    except Exception as e:
        db.rollback()
        flash(f"Hata: {str(e)}", "danger")
    finally:
        cursor.close()
        db.close()
    return redirect(url_for('siparis_listesi'))

@app.route('/siparis-adet-guncelle/<int:detay_id>', methods=['POST'])
def siparis_adet_guncelle(detay_id):
    yeni_adet = int(request.form.get('adet', 1))
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT sd.Siparis_Id, u.Fiyat, sd.Adet FROM siparis_detay sd JOIN urun u ON sd.Urun_Id = u.Urun_Id WHERE sd.Detay_Id = %s", (detay_id,))
        detay = cursor.fetchone()
        if detay:
            eski_ara_toplam = detay['Fiyat'] * detay['Adet']
            yeni_ara_toplam = detay['Fiyat'] * yeni_adet
            fark = yeni_ara_toplam - eski_ara_toplam
            cursor.execute("UPDATE siparis_detay SET Adet = %s, Ara_Toplam = %s WHERE Detay_Id = %s", (yeni_adet, yeni_ara_toplam, detay_id))
            cursor.execute("UPDATE SIPARIS SET Tutar = Tutar + %s WHERE Siparis_Id = %s", (fark, detay['Siparis_Id']))
            db.commit()
            flash("Adet güncellendi.", "success")
    except Exception as e:
        db.rollback()
        flash(f"Hata: {str(e)}", "danger")
    finally:
        cursor.close()
        db.close()
    return redirect(url_for('siparis_listesi'))

@app.route('/siparis-detay-sil/<int:detay_id>', methods=['POST'])
def siparis_detay_sil(detay_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT Siparis_Id, Ara_Toplam FROM siparis_detay WHERE Detay_Id = %s", (detay_id,))
        detay = cursor.fetchone()
        if detay:
            cursor.execute("DELETE FROM siparis_detay WHERE Detay_Id = %s", (detay_id,))
            cursor.execute("UPDATE SIPARIS SET Tutar = Tutar - %s WHERE Siparis_Id = %s", (detay['Ara_Toplam'], detay['Siparis_Id']))
            db.commit()
            flash("Ürün kaldırıldı.", "success")
    except Exception as e:
        db.rollback()
        flash(f"Hata: {str(e)}", "danger")
    finally:
        cursor.close()
        db.close()
    return redirect(url_for('siparis_listesi'))

# --- HESAP KAPATMA ---
@app.route('/adisyon-kapat/<int:siparis_id>', methods=['POST'])
def adisyon_kapat(siparis_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        # Kısıtlamalara takılmamak için foreign key kontrollerini geçici kapatıyoruz
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")

        cursor.execute("SELECT Masa_Id FROM SIPARIS WHERE Siparis_Id = %s", (siparis_id,))
        siparis = cursor.fetchone()
        if siparis:
            masa_id = siparis['Masa_Id']
            
            # Masadaki aktif müşteriyi buluyoruz
            cursor.execute("SELECT Musteri_ID FROM REZERVASYON WHERE Masa_Id = %s", (masa_id,))
            rezervasyon = cursor.fetchone()
            
            # 1. Sipariş Kalemlerini ve Siparişi temizliyoruz
            cursor.execute("DELETE FROM siparis_detay WHERE Siparis_Id = %s", (siparis_id,))
            cursor.execute("DELETE FROM SIPARIS WHERE Siparis_Id = %s", (siparis_id,))
            
            # 2. Bağlı müşteri ve rezervasyon varsa komple uçuruyoruz (Masa tamamen boşalıyor)
            if rezervasyon and rezervasyon['Musteri_ID']:
                musteri_id = rezervasyon['Musteri_ID']
                cursor.execute("DELETE FROM REZERVASYON WHERE Musteri_ID = %s", (musteri_id,))
                cursor.execute("DELETE FROM MUSTERI WHERE Musteri_ID = %s", (musteri_id,))
            else:
                cursor.execute("DELETE FROM REZERVASYON WHERE Masa_Id = %s", (masa_id,))
            
            db.commit()
            flash("💳 Hesap Ödendi! Masa boşaltıldı, siparişler ve rezervasyon veritabanından tamamen temizlendi.", "success")
        
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")
    except Exception as e:
        db.rollback()
        try: cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")
        except: pass
        flash(f"Hata: {str(e)}", "danger")
    finally:
        cursor.close()
        db.close()
    return redirect(url_for('siparis_listesi'))

@app.route('/musteriler')
def musteri_listesi():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    query = """
        SELECT M.Musteri_ID, M.Ad, M.Soyad, R.Tarih, R.Masa_ID 
        FROM MUSTERI M 
        LEFT JOIN REZERVASYON R ON M.Musteri_ID = R.Musteri_ID 
        ORDER BY M.Musteri_ID DESC
    """
    cursor.execute(query)
    musteriler = cursor.fetchall()
    cursor.close()
    db.close()
    return render_template('musteri.html', musteriler=musteriler)

@app.route('/rezervasyon_guncelle/<int:musteri_id>', methods=['POST'])
def rezervasyon_guncelle(musteri_id):
    tarih = request.form.get('tarih')
    saat = request.form.get('saat')
    masa_id = request.form.get('masa_id')
    tam_tarih = f"{tarih} {saat}:00"
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("SELECT Rezerve_ID FROM REZERVASYON WHERE Musteri_ID = %s", (musteri_id,))
        rez = cursor.fetchone()
        if rez:
            cursor.execute("UPDATE REZERVASYON SET Tarih = %s, Masa_ID = %s WHERE Musteri_ID = %s", (tam_tarih, masa_id, musteri_id))
        else:
            cursor.execute("INSERT INTO REZERVASYON (Tarih, Masa_ID, Musteri_ID, Kisi_Sayisi) VALUES (%s, %s, %s, 2)", (tam_tarih, masa_id, musteri_id))
        db.commit()
        flash("Müşteri rezervasyonu başarıyla güncellendi.", "success")
    except Exception as e:
        db.rollback()
        flash(f"Güncelleme Hatası: {str(e)}", "danger")
    finally:
        cursor.close()
        db.close()
    return redirect(url_for('musteri_listesi'))

@app.route('/musteri_sil/<int:musteri_id>', methods=['POST'])
def musteri_sil(musteri_id):
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("DELETE FROM REZERVASYON WHERE Musteri_ID = %s", (musteri_id,))
        cursor.execute("DELETE FROM MUSTERI WHERE Musteri_ID = %s", (musteri_id,))
        db.commit()
        flash("✨ Müşteri ve bağlı tüm rezervasyon masaları başarıyla silindi.", "success")
    except Exception as e:
        db.rollback()
        flash(f"Silme Hatası: {str(e)}", "danger")
    finally:
        cursor.close()
        db.close()
    return redirect(url_for('musteri_listesi'))

@app.route('/urun-ekle', methods=['POST'])
def urun_ekle():
    urun_adi = request.form.get('urun_adi')
    fiyat = request.form.get('fiyat')
    aciklama = request.form.get('aciklama')
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("INSERT INTO urun (Urun_Adi, Fiyat, Aciklama) VALUES (%s, %s, %s)", (urun_adi, fiyat, aciklama))
        db.commit()
        flash(f"✨ {urun_adi} başarıyla menüye eklendi.", "success")
    except Exception as e:
        db.rollback()
        flash(f"Ürün Ekleme Hatası: {str(e)}", "danger")
    finally:
        cursor.close()
        db.close()
    return redirect(url_for('menu_listesi'))

@app.route('/urun-guncelle/<int:urun_id>', methods=['POST'])
def urun_guncelle(urun_id):
    yeni_fiyat = request.form.get('fiyat')
    yeni_aciklama = request.form.get('aciklama')
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("UPDATE urun SET Fiyat = %s, Aciklama = %s WHERE Urun_Id = %s", (yeni_fiyat, yeni_aciklama, urun_id))
        db.commit()
        flash("Ürün bilgileri başarıyla güncellendi.", "success")
    except Exception as e:
        db.rollback()
        flash(f"Güncelleme Hatası: {str(e)}", "danger")
    finally:
        cursor.close()
        db.close()
    return redirect(url_for('menu_listesi'))

@app.route('/urun-sil/<int:urun_id>', methods=['POST'])
def urun_sil(urun_id):
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("DELETE FROM urun WHERE Urun_Id = %s", (urun_id,))
        cursor.execute("SET @count = 0;")
        cursor.execute("UPDATE urun SET Urun_Id = (@count:= @count + 1);")
        cursor.execute("ALTER TABLE urun AUTO_INCREMENT = 1;")
        db.commit()
        flash("Ürün silindi.", "success")
    except Exception as e:
        db.rollback()
        flash(f"Silme Hatası: {str(e)}", "danger")
    finally:
        cursor.close()
        db.close()
    return redirect(url_for('menu_listesi'))

# --- MASA EKLEME SİSTEMİ ---
@app.route('/masa-ekle', methods=['POST'])
def masa_ekle():
    kapasite = request.form.get('kapasite')
    konum = request.form.get('konum')
    if not kapasite or not konum:
        flash("❌ Hata: Kapasite ve Konum alanları boş bırakılamaz!", "danger")
        return redirect(url_for('masalar_listesi'))
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        id_query = """
            SELECT (CASE WHEN NOT EXISTS(SELECT 1 FROM MASA WHERE Masa_Id = 1) THEN 1
            ELSE COALESCE((SELECT MIN(m1.Masa_Id + 1) FROM MASA m1 
                           WHERE NOT EXISTS (SELECT 1 FROM MASA m2 WHERE m2.Masa_Id = m1.Masa_Id + 1)), 1)
            END) AS yeni_id
        """
        cursor.execute(id_query)
        res = cursor.fetchone()
        yeni_id = res['yeni_id'] if res else 1
        cursor.execute("INSERT INTO MASA (Masa_Id, Kapasite, Konum) VALUES (%s, %s, %s)", (yeni_id, kapasite, konum))
        db.commit()
        flash(f"✨ Masa {yeni_id} ({konum}) başarıyla sisteme eklendi.", "success")
    except Exception as e:
        db.rollback()
        flash(f"Masa Ekleme Hatası: {str(e)}", "danger")
    finally:
        cursor.close()
        db.close()
    return redirect(url_for('masalar_listesi'))

# --- MASA SİLME SİSTEMİ ---
@app.route('/masa-sil/<int:masa_id>', methods=['POST'])
def masa_sil(masa_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM SIPARIS WHERE Masa_Id = %s AND Tutar > 0", (masa_id,))
        if cursor.fetchone():
            flash(f"❌ Engel: Masa {masa_id} üzerinde aktif adisyon var! Önce hesabı kapatmalısınız.", "danger")
            return redirect(url_for('masalar_listesi'))
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
        query_detay_sil = "DELETE sd FROM siparis_detay sd INNER JOIN SIPARIS s ON sd.Siparis_Id = s.Siparis_Id WHERE s.Masa_Id = %s"
        cursor.execute(query_detay_sil, (masa_id,))
        cursor.execute("DELETE FROM SIPARIS WHERE Masa_Id = %s", (masa_id,))
        cursor.execute("DELETE FROM REZERVASYON WHERE Masa_Id = %s", (masa_id,))
        cursor.execute("DELETE FROM MASA WHERE Masa_Id = %s", (masa_id,))
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")
        db.commit()
        flash(f"🗑️ Masa {masa_id} ve bağlı tüm kayıtları başarıyla silindi.", "success")
    except Exception as e:
        db.rollback()
        try: cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")
        except: pass
        flash(f"Masa Silme Hatası: {str(e)}", "danger")
    finally:
        cursor.close()
        db.close()
    return redirect(url_for('masalar_listesi'))

if __name__ == '__main__':
    app.run(debug=True)