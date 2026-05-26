from django.db import models


class SystemConfig(models.Model):

    SENSITIVE_KEYS = {
        'wa_access_token', 'wa_app_secret', 'gemini_api_key',
        'safepay_merchant_key', 'safepay_secret_key',
        'bsecure_client_id', 'bsecure_client_secret',
        'stripe_secret_key', 'stripe_webhook_secret',
    }

    REQUIRED_KEYS = {
        'wa_access_token', 'wa_phone_number_id', 'wa_verify_token', 'gemini_api_key',
    }

    # Maps config keys → Django settings attribute names (env fallback)
    ENV_KEY_MAP = {
        'wa_access_token':       'WA_ACCESS_TOKEN',
        'wa_phone_number_id':    'WA_PHONE_NUMBER_ID',
        'wa_app_secret':         'WA_APP_SECRET',
        'wa_verify_token':       'WA_VERIFY_TOKEN',
        'wa_otp_template_name':  'WA_OTP_TEMPLATE_NAME',
        'wa_waba_id':            'WA_WABA_ID',
        'gemini_api_key':        'GEMINI_API_KEY',
        'openai_api_key':        'OPENAI_API_KEY',
        'gemini_model':          'GEMINI_MODEL',
        'ai_backend':            'AI_BACKEND',
        'base_url':              'BASE_URL',
        'safepay_merchant_key':  'SAFEPAY_MERCHANT_KEY',
        'safepay_secret_key':    'SAFEPAY_SECRET_KEY',
        'safepay_environment':   'SAFEPAY_ENVIRONMENT',
        'bsecure_client_id':     'BSECURE_CLIENT_ID',
        'bsecure_client_secret': 'BSECURE_CLIENT_SECRET',
        'bsecure_environment':   'BSECURE_ENVIRONMENT',
        'billing_gateway':       'BILLING_GATEWAY',
        'stripe_secret_key':     'STRIPE_SECRET_KEY',
        'stripe_webhook_secret': 'STRIPE_WEBHOOK_SECRET',
        'stripe_price_basic':    'STRIPE_PRICE_BASIC',
        'stripe_price_professional': 'STRIPE_PRICE_PROFESSIONAL',
        'stripe_price_enterprise':   'STRIPE_PRICE_ENTERPRISE',
        'stripe_price_basic_aed':         'STRIPE_PRICE_BASIC_AED',
        'stripe_price_professional_aed':  'STRIPE_PRICE_PROFESSIONAL_AED',
        'stripe_price_enterprise_aed':    'STRIPE_PRICE_ENTERPRISE_AED',
        'stripe_price_basic_usd':         'STRIPE_PRICE_BASIC_USD',
        'stripe_price_professional_usd':  'STRIPE_PRICE_PROFESSIONAL_USD',
        'stripe_price_enterprise_usd':    'STRIPE_PRICE_ENTERPRISE_USD',
    }

    DEFAULTS = {
        'wa_access_token':            '',
        'wa_phone_number_id':         '',
        'wa_verify_token':            '',
        'wa_app_secret':              '',
        'wa_otp_template_name':       'otp_verification',
        'wa_waba_id':                 '',
        'gemini_api_key':             '',
        'gemini_model':               'gemini-2.5-flash-lite',
        'ai_backend':                 'gemini',
        'base_url':                   'http://localhost:8000',
        'active_payment_gateway':     'manual',
        'jazzcash_number':            '',
        'easypaisa_number':           '',
        'bank_account_number':        '',
        'bank_account_name':          '',
        'safepay_merchant_key':       '',
        'safepay_secret_key':         '',
        'safepay_environment':        'sandbox',
        'bsecure_client_id':          '',
        'bsecure_client_secret':      '',
        'bsecure_environment':        'sandbox',
        'billing_gateway':            'manual',
        'stripe_secret_key':          '',
        'stripe_webhook_secret':      '',
        'stripe_price_basic':         '',
        'stripe_price_professional':  '',
        'stripe_price_enterprise':    '',
        'billing_price_basic_pkr':        '13000',
        'billing_price_professional_pkr': '40000',
        'billing_price_enterprise_pkr':   '120000',
        'stripe_price_basic_aed':         '',
        'stripe_price_professional_aed':  '',
        'stripe_price_enterprise_aed':    '',
        'billing_price_basic_aed':        '299',
        'billing_price_professional_aed': '899',
        'billing_price_enterprise_aed':   '2699',
        'stripe_price_basic_usd':         '',
        'stripe_price_professional_usd':  '',
        'stripe_price_enterprise_usd':    '',
        'billing_price_basic_usd':        '49',
        'billing_price_professional_usd': '149',
        'billing_price_enterprise_usd':   '449',
        'feature_property_search':    'true',
        'feature_property_listing':   'true',
        'feature_tax_advice':         'true',
        'feature_loan_eligibility':   'true',
        'feature_scam_check':         'true',
        'feature_document_verification': 'true',
        'feature_property_audit':     'true',
        'feature_talk_to_agent':      'true',
        'feature_deal_lock':          'true',
        'feature_voice_messages':     'true',
        'feature_follow_up_automation': 'false',
        'feature_auto_assign':          'false',
        'scraper_search_enabled':     'true',
        'use_membership_rbac':        'true',
    }

    key        = models.CharField(max_length=100, unique=True, db_index=True)
    value      = models.TextField(blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        'users.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='config_changes',
    )

    class Meta:
        app_label = 'sysconfig'
        db_table  = 'system_config'
        verbose_name = 'System Config'

    def __str__(self):
        return self.key
