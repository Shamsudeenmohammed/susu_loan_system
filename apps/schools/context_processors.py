from .models import School
from .scoping import get_active_school, is_global_user


def school_context(request):
    """Expose the active school and any switchable schools to all templates."""
    user = getattr(request, 'user', None)
    active_school = get_active_school(request)
    switchable = []
    global_user = False
    if getattr(user, 'is_authenticated', False):
        global_user = is_global_user(user)
        if global_user:
            switchable = list(School.objects.filter(is_active=True))
    return {
        'active_school': active_school,
        'switchable_schools': switchable,
        'is_global_user': global_user,
    }
