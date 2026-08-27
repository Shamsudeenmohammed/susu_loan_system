from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .api_views import (
    CustomerViewSet, SusuAccountViewSet, TransactionViewSet,
    LoanViewSet, LoanRepaymentViewSet, loan_eligibility_api
)

router = DefaultRouter()
router.register(r'customers', CustomerViewSet)
router.register(r'susu-accounts', SusuAccountViewSet)
router.register(r'transactions', TransactionViewSet)
router.register(r'loans', LoanViewSet)
router.register(r'repayments', LoanRepaymentViewSet)

urlpatterns = [
    path('v1/', include(router.urls)),
    path('v1/eligibility/', loan_eligibility_api, name='api_loan_eligibility'),
]
