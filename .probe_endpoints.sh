#!/usr/bin/env bash
# Probe candidate national WCS/WMS GetCapabilities endpoints across Asia,
# Middle East, and South America (non-Brazil). Reports HTTP status + whether
# the body looks like an OGC capabilities XML. Verify-first: only endpoints
# that respond with real capabilities are worth building connectors for.
set -u
UA="CAS/0.1 (Community Attribute Service)"

probe() {
  local name="$1" url="$2"
  # -m 25 timeout; capture status + first bytes
  local out status head
  out=$(curl -sS -m 25 -A "$UA" -w $'\n__STATUS__%{http_code}' "$url" 2>/dev/null)
  status=$(printf '%s' "$out" | sed -n 's/.*__STATUS__//p' | tail -1)
  body=$(printf '%s' "$out" | sed 's/__STATUS__[0-9]*$//')
  # classify
  local kind="?"
  if printf '%s' "$body" | grep -qiE "WCS_Capabilities|Capabilities .*wcs|CoverageOffering|wcs:Capabilities"; then kind="WCS-CAPS"
  elif printf '%s' "$body" | grep -qiE "WMS_Capabilities|WMT_MS_Capabilities|<Layer"; then kind="WMS-CAPS"
  elif printf '%s' "$body" | grep -qiE "ServiceExceptionReport|ExceptionReport"; then kind="OGC-EXC"
  elif printf '%s' "$body" | grep -qiE "<\?xml|<html|json|ESRI|ArcGIS"; then kind="OTHER-XML/HTML"
  fi
  local n; n=$(printf '%s' "$body" | wc -c | tr -d ' ')
  printf '%-22s %-10s %-14s %6sB  %s\n' "$name" "$status" "$kind" "$n" "$url"
}

echo "################ ASIA ################"
probe thailand_gistda   "https://gistdaportal.gistda.or.th/data/geoserver/ows?service=WCS&version=2.0.1&request=GetCapabilities"
probe thailand_ldd      "https://geoservices.ldd.go.th/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"
probe philippines_namria "https://geoserver.namria.gov.ph/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"
probe philippines_geop  "https://www.geoportal.gov.ph/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"
probe vietnam_vea       "https://geoservice.monre.gov.vn/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"
probe malaysia_mygeo    "https://geoportal.mygeoportal.gov.my/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"
probe indonesia_big     "https://geoservices.big.go.id/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"
probe indonesia_ina     "https://tanahair.indonesia.go.id/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"
probe nepal_icimod      "https://geoapps.icimod.org/arcgis/rest/services?f=json"
probe srilanka_survey   "https://geoportal.survey.gov.lk/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"
probe pakistan_suparco  "https://geoportal.suparco.gov.pk/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"
probe mongolia          "https://geoportal.gov.mn/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"

echo "################ MIDDLE EAST ################"
probe turkey_hgm        "https://cbsservis.hgm.msb.gov.tr/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"
probe turkey_atlas      "https://atlas.harita.gov.tr/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"
probe israel_govmap     "https://open.govmap.gov.il/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"
probe israel_survey     "https://mapi.gov.il/arcgis/rest/services?f=json"
probe uae               "https://geoportal.bayanat.ae/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"
probe saudi_gov         "https://maps.saudicensus.sa/server/rest/services?f=json"
probe iran_ncc          "https://maps.ncc.gov.ir/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"
probe jordan            "https://geoportal.rjgc.gov.jo/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"

echo "################ SOUTH AMERICA (non-Brazil) ################"
probe chile_ide         "https://www.ide.cl/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"
probe chile_idecyt      "https://catalogo.geoportal.cl/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"
probe ecuador_geo       "https://www.geoportaligm.gob.ec/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"
probe ecuador_sni       "https://geoportal.sni.gob.ec/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"
probe bolivia_geo       "https://geo.gob.bo/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"
probe uruguay_ide       "https://www.gub.uy/infraestructura-datos-espaciales/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"
probe paraguay_infona   "https://geo.infona.gov.py/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"
probe venezuela         "https://geoportalsb.minaguas.gob.ve/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"
probe argentina_ign     "https://wms.ign.gob.ar/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"
probe argentina_idera   "https://geoservicios.indec.gov.ar/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"
probe colombia_ideam    "https://geoservicios.ideam.gov.co/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities"

echo "################ DONE ################"
