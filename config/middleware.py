class StripNullOriginMiddleware:
    """Remove the 'null' Origin header so CSRF origin check passes.

    Some browsers / privacy extensions send Origin: null instead of the real
    origin for same-origin form POSTs, which causes Django's CSRF check to
    reject the request. Stripping it lets Django fall back to the Referer
    header or accept the request outright.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.META.get('HTTP_ORIGIN') == 'null':
            del request.META['HTTP_ORIGIN']
        return self.get_response(request)
