"""SEO: structured data, robots.txt, sitemap.xml, web manifest, security.txt, favicon route."""
import json
from datetime import date

from flask import current_app, send_from_directory

from config import Config as cfg

SITE = cfg.SITE_URL
TITLE = f"{cfg.SITE_NAME}: vote for your favourite presidential candidate "
OG_IMAGE = f"{SITE}/static/og-image.png"

FAQ = [
    ("How much does it cost to vote?",
     f"Each vote costs KES {cfg.VOTE_PRICE_KES}. You can vote more than once; every paid vote is counted."),
    ("How do I pay?",
     "After you choose a candidate you are sent to IntaSend's secure payment page, where you can pay with M-Pesa or a card."),
    ("When is my vote counted?",
     "Your vote is counted as soon as IntaSend confirms the payment, usually within seconds."),
    ("Is this an official poll?",
     "No. This is an informal fan poll. Paid votes are not a scientific measure of public opinion."),
]


def site_context():
    return {"name": cfg.SITE_NAME, "url": SITE, "title": TITLE, "description": cfg.SITE_DESCRIPTION,
            "og_image": OG_IMAGE, "twitter": cfg.TWITTER_HANDLE, "price": cfg.VOTE_PRICE_KES}


def home_schema(results):
    org, web, page = f"{SITE}/#org", f"{SITE}/#website", f"{SITE}/#webpage"
    graph = [
        {"@type": "Organization", "@id": org, "name": cfg.SITE_NAME, "url": SITE + "/",
         "logo": {"@type": "ImageObject", "url": f"{SITE}/static/icon-512.png", "width": 512, "height": 512}},
        {"@type": "WebSite", "@id": web, "url": SITE + "/", "name": cfg.SITE_NAME,
         "inLanguage": "en-KE", "publisher": {"@id": org}},
        {"@type": "WebPage", "@id": page, "url": SITE + "/", "name": TITLE,
         "description": cfg.SITE_DESCRIPTION, "isPartOf": {"@id": web}, "inLanguage": "en-KE",
         "primaryImageOfPage": {"@type": "ImageObject", "url": OG_IMAGE},
         "dateModified": date.today().isoformat()},
        {"@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in FAQ]},
    ]
    if results:
        graph.append({"@type": "ItemList", "name": "Presidential candidates in the poll",
                      "itemListElement": [{"@type": "ListItem", "position": i, "item": {
                          "@type": "Person", "name": r["name"],
                          **({"affiliation": {"@type": "Organization", "name": r["party"]}} if r["party"] else {})}}
                          for i, r in enumerate(results, 1)]})
    return {"@context": "https://schema.org", "@graph": graph}


def register(app):
    @app.context_processor
    def _site():
        return {"site": site_context()}

    @app.get("/robots.txt")
    def robots():
        lines = ["User-agent: *", "Allow: /", "Disallow: /admin", "Disallow: /vote", "Disallow: /api",
                 "Disallow: /webhooks", "", f"Sitemap: {SITE}/sitemap.xml"]
        return current_app.response_class("\n".join(lines) + "\n", mimetype="text/plain")

    @app.get("/sitemap.xml")
    def sitemap():
        xml = ('<?xml version="1.0" encoding="UTF-8"?>'
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
               'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">'
               f'<url><loc>{SITE}/</loc><lastmod>{date.today().isoformat()}</lastmod>'
               '<changefreq>daily</changefreq><priority>1.0</priority>'
               f'<image:image><image:loc>{OG_IMAGE}</image:loc></image:image></url></urlset>')
        return current_app.response_class(xml, mimetype="application/xml")

    @app.get("/site.webmanifest")
    def manifest():
        data = {"name": cfg.SITE_NAME, "short_name": cfg.SITE_NAME, "start_url": "/", "display": "standalone",
                "background_color": "#F7F5F0", "theme_color": "#0B5D3B", "lang": "en-KE",
                "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                          {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}]}
        return current_app.response_class(json.dumps(data), mimetype="application/manifest+json")

    @app.get("/favicon.ico")
    def favicon():
        return send_from_directory(app.static_folder, "favicon-32.png", mimetype="image/png")

    @app.get("/.well-known/security.txt")
    def security_txt():
        if not cfg.SECURITY_CONTACT:
            return ("", 404)
        body = f"Contact: {cfg.SECURITY_CONTACT}\nCanonical: {SITE}/.well-known/security.txt\nPreferred-Languages: en\n"
        return current_app.response_class(body, mimetype="text/plain")
