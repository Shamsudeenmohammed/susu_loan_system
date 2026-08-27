from rest_framework import serializers, viewsets, permissions
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from apps.customers.models import Customer
from apps.susu.models import SusuAccount
from apps.payments.models import Transaction
from apps.loans.models import Loan, LoanRepayment


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ['pk', 'customer_number', 'first_name', 'last_name', 'phone', 'email', 'status', 'created_at']
        read_only_fields = ['customer_number', 'created_at']


class SusuAccountSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source='customer.get_full_name', read_only=True)

    class Meta:
        model = SusuAccount
        fields = ['pk', 'account_number', 'customer', 'customer_name',
                  'contribution_frequency', 'current_balance', 'status', 'opened_at']
        read_only_fields = ['account_number', 'current_balance', 'opened_at']


class TransactionSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source='customer.get_full_name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)

    class Meta:
        model = Transaction
        fields = ['pk', 'transaction_number', 'customer', 'customer_name',
                  'account', 'transaction_type', 'amount', 'balance_before',
                  'balance_after', 'payment_method', 'reference', 'description',
                  'created_by_name', 'created_at']
        read_only_fields = ['transaction_number', 'balance_before', 'balance_after', 'created_at']


class LoanSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source='customer.get_full_name', read_only=True)
    product_name = serializers.CharField(source='loan_product.name', read_only=True)

    class Meta:
        model = Loan
        fields = ['pk', 'loan_number', 'customer', 'customer_name',
                  'loan_product', 'product_name', 'principal_amount', 'interest_amount',
                  'total_amount', 'outstanding_balance', 'status', 'application_date',
                  'approval_date', 'disbursement_date']
        read_only_fields = ['loan_number', 'created_at']


class LoanRepaymentSerializer(serializers.ModelSerializer):
    loan_number = serializers.CharField(source='loan.loan_number', read_only=True)

    class Meta:
        model = LoanRepayment
        fields = ['pk', 'repayment_number', 'loan', 'loan_number', 'amount',
                  'payment_method', 'reference', 'created_at']
        read_only_fields = ['repayment_number', 'created_at']


class IsStaffOrReadOnly(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return request.user.is_authenticated
        return request.user.is_authenticated and request.user.is_staff_member


class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
    permission_classes = [IsStaffOrReadOnly]
    filterset_fields = ['status']
    search_fields = ['customer_number', 'first_name', 'last_name', 'phone']

    def perform_create(self, serializer):
        serializer.save(registered_by=self.request.user)


class SusuAccountViewSet(viewsets.ModelViewSet):
    queryset = SusuAccount.objects.select_related('customer')
    serializer_class = SusuAccountSerializer
    permission_classes = [IsStaffOrReadOnly]
    filterset_fields = ['status', 'contribution_frequency']

    def perform_create(self, serializer):
        serializer.save(opened_by=self.request.user)


class TransactionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Transaction.objects.select_related('customer', 'created_by')
    serializer_class = TransactionSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['transaction_type', 'payment_method']
    search_fields = ['transaction_number', 'customer__first_name', 'customer__last_name']


class LoanViewSet(viewsets.ModelViewSet):
    queryset = Loan.objects.select_related('customer', 'loan_product')
    serializer_class = LoanSerializer
    permission_classes = [IsStaffOrReadOnly]
    filterset_fields = ['status']
    search_fields = ['loan_number', 'customer__first_name', 'customer__last_name']

    def perform_create(self, serializer):
        loan = serializer.save(submitted_by=self.request.user)
        loan.calculate_financials()
        loan.outstanding_balance = loan.total_amount
        loan.save()


class LoanRepaymentViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = LoanRepayment.objects.select_related('loan')
    serializer_class = LoanRepaymentSerializer
    permission_classes = [permissions.IsAuthenticated]


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def loan_eligibility_api(request):
    """REST API endpoint for loan eligibility check."""
    customer = getattr(request.user, 'customer_profile', None)
    if not customer:
        if request.user.is_staff_member:
            customer_id = request.query_params.get('customer_id')
            if not customer_id:
                return Response({'error': 'customer_id parameter required for staff.'}, status=400)
            try:
                customer = Customer.objects.get(pk=customer_id)
            except Customer.DoesNotExist:
                return Response({'error': 'Customer not found.'}, status=404)
        else:
            return Response({'error': 'No customer profile found.'}, status=400)

    from apps.loans.eligibility import LoanEligibilityService
    result, audit = LoanEligibilityService.check_eligibility(customer)

    response_data = {
        'eligible': result.eligible,
        'status': 'ELIGIBLE' if result.eligible else 'NOT ELIGIBLE',
        'eligibility_score': result.score,
        'maximum_loan_amount': str(result.maximum_loan_amount),
        'membership_months': audit.membership_months,
        'required_membership_months': audit.required_membership_months,
        'successful_contributions': audit.successful_contributions,
        'required_contributions': audit.required_contributions,
        'savings': str(audit.total_savings),
        'minimum_savings': str(audit.minimum_savings),
        'active_loans': audit.active_loans,
        'missed_periods': audit.missed_periods,
        'max_missed_periods': audit.max_missed_periods,
        'has_overdue': audit.has_overdue,
        'is_kyc_complete': audit.is_kyc_complete,
        'passed_criteria': result.passed_criteria,
        'failed_criteria': result.failed_criteria,
        'reasons': result.reasons,
    }
    return Response(response_data)
