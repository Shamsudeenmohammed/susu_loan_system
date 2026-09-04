from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Q, Sum
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.conf import settings
from django.urls import reverse
from decimal import Decimal
from apps.core.decorators import role_required
from .models import Transaction, Withdrawal
from .forms import ContributionForm, WithdrawalRequestForm, WithdrawalReviewForm
from .services import record_contribution, record_withdrawal
from apps.susu.models import SusuAccount
from apps.core.utils import generate_unique_number


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'CASHIER', 'COLLECTOR')
def transaction_list(request):
    transactions = Transaction.objects.select_related('customer', 'account', 'created_by').all()

    tx_type = request.GET.get('type')
    if tx_type:
        transactions = transactions.filter(transaction_type=tx_type)

    context = {
        'transactions': transactions[:100],
        'total_count': transactions.count(),
    }
    return render(request, 'payments/transaction_list.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'CASHIER', 'COLLECTOR')
def record_contribution_view(request):
    if request.method == 'POST':
        form = ContributionForm(request.POST)
        if form.is_valid():
            customer = form.cleaned_data['customer']
            amount = form.cleaned_data['amount']
            payment_method = form.cleaned_data['payment_method']
            reference = form.cleaned_data['reference']
            notes = form.cleaned_data['notes']

            txn, success, error = record_contribution(
                customer=customer,
                amount=amount,
                payment_method=payment_method,
                created_by=request.user,
                reference=reference,
                notes=notes,
            )

            if success:
                messages.success(
                    request,
                    f'Contribution of GHS {amount:.2f} recorded. '
                    f'Transaction: {txn.transaction_number}'
                )

                from apps.notifications.tasks import send_contribution_sms
                try:
                    send_contribution_sms.delay(txn.pk)
                except Exception:
                    pass

                return redirect('transaction_detail', pk=txn.pk)
            else:
                messages.error(request, error)
    else:
        form = ContributionForm()
    return render(request, 'payments/contribution_form.html', {'form': form})


@login_required
def transaction_detail(request, pk):
    txn = get_object_or_404(
        Transaction.objects.select_related('customer', 'account', 'created_by'),
        pk=pk
    )
    if request.user.has_role('CUSTOMER'):
        if not request.user.customer_profile or request.user.customer_profile.pk != txn.customer.pk:
            messages.error(request, 'Access denied.')
            return redirect('customer_dashboard')
    return render(request, 'payments/transaction_detail.html', {'transaction': txn})


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'CASHIER')
def withdrawal_list(request):
    withdrawals = Withdrawal.objects.select_related('customer', 'account', 'requested_by', 'reviewed_by').all()
    status = request.GET.get('status')
    if status:
        withdrawals = withdrawals.filter(status=status)
    return render(request, 'payments/withdrawal_list.html', {
        'withdrawals': withdrawals,
    })


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'CASHIER', 'CUSTOMER')
def withdrawal_request_view(request):
    customer = request.user.customer_profile if request.user.has_role('CUSTOMER') else None

    if request.method == 'POST':
        form = WithdrawalRequestForm(request.POST, customer=customer)
        if form.is_valid():
            withdrawal = form.save(commit=False)
            withdrawal.requested_by = request.user
            if customer:
                withdrawal.customer = customer
            else:
                withdrawal.customer = form.cleaned_data['account'].customer
            withdrawal.save()
            messages.success(request, f'Withdrawal request {withdrawal.withdrawal_number} submitted.')

            from apps.notifications.tasks import send_withdrawal_request_sms
            try:
                send_withdrawal_request_sms.delay(withdrawal.pk)
            except Exception:
                pass

            if request.user.has_role('CUSTOMER'):
                return redirect('customer_dashboard')
            return redirect('withdrawal_list')
    else:
        form = WithdrawalRequestForm(customer=customer)
    return render(request, 'payments/withdrawal_form.html', {'form': form})


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def withdrawal_review(request, pk):
    withdrawal = get_object_or_404(Withdrawal, pk=pk)
    if request.method == 'POST':
        form = WithdrawalReviewForm(request.POST)
        if form.is_valid():
            action = form.cleaned_data['status']
            notes = form.cleaned_data['review_notes']

            withdrawal.reviewed_by = request.user
            withdrawal.review_notes = notes

            if action == 'APPROVED':
                withdrawal.status = Withdrawal.Status.APPROVED
                withdrawal.save()

                txn, success, error = record_withdrawal(withdrawal, request.user)
                if success:
                    messages.success(request, f'Withdrawal {withdrawal.withdrawal_number} approved and completed.')
                    try:
                        from apps.notifications.tasks import send_withdrawal_status_sms
                        send_withdrawal_status_sms.delay(withdrawal.pk, 'APPROVED')
                    except Exception:
                        pass
                else:
                    messages.error(request, f'Approval succeeded but processing failed: {error}')
            else:
                withdrawal.status = Withdrawal.Status.REJECTED
                withdrawal.save()
                messages.info(request, f'Withdrawal {withdrawal.withdrawal_number} rejected.')
                try:
                    from apps.notifications.tasks import send_withdrawal_status_sms
                    send_withdrawal_status_sms.delay(withdrawal.pk, 'REJECTED')
                except Exception:
                    pass

            return redirect('withdrawal_list')
    else:
        form = WithdrawalReviewForm()
    return render(request, 'payments/withdrawal_review.html', {
        'form': form,
        'withdrawal': withdrawal,
    })


@login_required
def customer_transactions(request):
    """Customer view of their own transactions."""
    if not hasattr(request.user, 'customer_profile'):
        messages.error(request, 'No customer profile found.')
        return redirect('dashboard')

    customer = request.user.customer_profile
    transactions = Transaction.objects.filter(
        customer=customer
    ).select_related('account', 'created_by').order_by('-created_at')

    total_contributions = transactions.filter(
        transaction_type='SUSU_CONTRIBUTION'
    ).aggregate(total=Sum('amount'))['total'] or 0

    total_withdrawals = transactions.filter(
        transaction_type='WITHDRAWAL'
    ).aggregate(total=Sum('amount'))['total'] or 0

    context = {
        'transactions': transactions[:50],
        'total_contributions': total_contributions,
        'total_withdrawals': total_withdrawals,
    }
    return render(request, 'payments/customer_transactions.html', context)


@login_required
def customer_contribute(request):
    """Customer self-service contribution via Paystack."""
    if not hasattr(request.user, 'customer_profile'):
        messages.error(request, 'No customer profile found.')
        return redirect('customer_dashboard')

    customer = request.user.customer_profile
    susu_accounts = SusuAccount.objects.filter(customer=customer, status='ACTIVE')

    # Paystack requires a valid email address. Prefer the customer's own, then
    # the account email, then fall back to the first available staff/admin
    # email so payments still initialize when a customer has no email set.
    email = customer.email or request.user.email
    if not email:
        from apps.accounts.models import User as AccountUser
        email = (
            AccountUser.objects.filter(is_staff=True)
            .exclude(email__isnull=True)
            .exclude(email='')
            .order_by('id')
            .values_list('email', flat=True)
            .first()
            or f"{request.user.username}@customer.zemzem.local"
        )

    if not susu_accounts.exists():
        messages.warning(request, 'You have no active susu account. Please contact support.')
        return redirect('customer_dashboard')

    if request.method == 'POST':
        account_pk = request.POST.get('account')
        amount_str = request.POST.get('amount', '0')

        try:
            amount = Decimal(amount_str)
        except Exception:
            messages.error(request, 'Invalid amount.')
            return redirect('customer_contribute')

        if amount <= 0:
            messages.error(request, 'Amount must be greater than zero.')
            return redirect('customer_contribute')

        account = get_object_or_404(SusuAccount, pk=account_pk, customer=customer, status='ACTIVE')

        reference = f"SUSU-{account.pk}-{generate_unique_number('PS')}"
        callback_url = f"{settings.SITE_URL}/payments/contribute/callback/"

        from .paystack import initialize_payment
        result = initialize_payment(
            amount=amount,
            email=email,
            reference=reference,
            callback_url=callback_url,
            metadata={
                'customer_id': customer.pk,
                'account_id': account.pk,
                'amount': str(amount),
            }
        )

        if result['status']:
            request.session['pending_contribution'] = {
                'account_id': account.pk,
                'amount': str(amount),
                'customer_id': customer.pk,
                'reference': reference,
            }
            return redirect(result['authorization_url'])
        else:
            messages.error(request, result.get('message', 'Payment initialization failed.'))
            return redirect('customer_contribute')

    context = {
        'susu_accounts': susu_accounts,
        'paystack_public_key': getattr(settings, 'PAYSTACK_PUBLIC_KEY', ''),
        'paystack_email': email,
    }
    return render(request, 'payments/customer_contribute.html', context)


@login_required
def customer_contribute_callback(request):
    """Paystack callback — record contribution, notify customer."""
    import logging
    logger = logging.getLogger('apps.payments')
    from .paystack import verify_payment

    pending = request.session.pop('pending_contribution', None)
    logger.info("Callback hit. user=%s has_pending=%s", request.user.pk, bool(pending))

    paystack_ref = (
        request.GET.get('reference', '')
        or request.GET.get('trxref', '')
        or request.GET.get('ref', '')
    )
    our_ref = (pending or {}).get('reference', '')

    customer = request.user.customer_profile

    # --- Resolve customer + account + amount ---
    amount = None
    account = None
    verified_ref = paystack_ref or our_ref

    # 1. Try Paystack API verification FIRST — recovers from session loss,
    #    and returns authoritative amount + metadata (works in test AND prod).
    verify_result = None
    for ref_candidate in [paystack_ref, our_ref]:
        if ref_candidate:
            verify_result = verify_payment(ref_candidate)
            if verify_result.get('status'):
                verified_ref = ref_candidate
                logger.info("Verified via API. ref=%s amount=%.2f", ref_candidate, float(verify_result['amount']))
                break
            verify_result = None

    if verify_result and verify_result.get('status') and verify_result.get('amount', 0) > 0:
        amount = verify_result['amount']
        meta = verify_result.get('metadata') or {}
        if not account:
            account = SusuAccount.objects.filter(pk=meta.get('account_id'), status='ACTIVE').first()
        if not customer:
            try:
                from apps.customers.models import Customer
                customer = Customer.objects.get(pk=meta.get('customer_id'))
            except Exception:
                pass

    # 2. Fall back to session data (dev/test convenience).
    if amount is None and pending and settings.DEBUG:
        amount = Decimal(pending.get('amount', '0'))

    if amount is None or amount <= 0:
        logger.warning("Could not resolve payment amount. paystack_ref=%s our_ref=%s", paystack_ref, our_ref)
        messages.warning(request, 'Your payment is being processed. Your balance will update shortly.')
        return redirect(f"{reverse('customer_dashboard')}?payment_pending=1")

    # Resolve account from session if API metadata was unavailable.
    if not account and pending:
        try:
            account = SusuAccount.objects.get(
                pk=pending.get('account_id'), customer=customer, status='ACTIVE'
            )
        except (SusuAccount.DoesNotExist, TypeError, ValueError):
            pass

    if not account or not customer:
        logger.error("Callback could not resolve account/customer. user=%s", request.user.pk)
        messages.error(request, 'Unable to identify your account. Please contact support.')
        return redirect('customer_dashboard')

    # --- Idempotency guard ---
    existing = Transaction.objects.filter(idempotency_key=verified_ref).first()
    if not existing and our_ref and our_ref != verified_ref:
        existing = Transaction.objects.filter(idempotency_key=our_ref).first()
    if existing:
        logger.info("Duplicate — txn %s exists. ref=%s", existing.transaction_number, verified_ref)
        messages.info(request, f'Payment already recorded. Transaction: {existing.transaction_number}')
        return redirect('transaction_detail', pk=existing.pk)

    # --- Record contribution (updates balance atomically) ---
    txn, success, error = record_contribution(
        customer=customer,
        amount=amount,
        payment_method=Transaction.PaymentMethod.PAYSTACK,
        created_by=request.user,
        reference=verified_ref,
        notes=f'Online contribution via Paystack. Ref: {verified_ref}',
        account=account,
    )

    if not success:
        logger.error("record_contribution failed: %s customer=%s", error, customer.pk)
        messages.error(request, error or 'Failed to record contribution.')
        return redirect('customer_contribute')

    txn.idempotency_key = verified_ref
    txn.save(update_fields=['idempotency_key'])

    # --- Async SMS via Sailup (financial transaction already saved) ---
    from apps.notifications.tasks import send_contribution_sms
    send_contribution_sms.delay(txn.pk)

    logger.info(
        "Contribution recorded. txn=%s amount=%.2f balance=%.2f customer=%s",
        txn.transaction_number, float(amount), float(txn.balance_after), customer.pk,
    )
    messages.success(
        request,
        f'Payment of GHS {amount:.2f} successful! New balance: GHS {txn.balance_after:.2f}'
    )
    return redirect('transaction_detail', pk=txn.pk)


@login_required
def payment_check(request):
    """
    AJAX endpoint — customer checks if a pending Paystack payment has been recorded.
    GET /payments/check/?account_id=X returns JSON with latest transaction info.
    """
    import logging
    logger = logging.getLogger('apps.payments')

    if not hasattr(request.user, 'customer_profile'):
        return JsonResponse({'status': 'error', 'message': 'No customer profile'}, status=400)

    customer = request.user.customer_profile
    account_id = request.GET.get('account_id')

    latest_txn = Transaction.objects.filter(
        customer=customer,
        transaction_type='SUSU_CONTRIBUTION',
    ).order_by('-created_at').first()

    if not latest_txn:
        return JsonResponse({
            'status': 'ok',
            'has_transaction': False,
            'balance': str(SusuAccount.objects.filter(customer=customer, status='ACTIVE').aggregate(t=Sum('current_balance'))['t'] or '0.00'),
        })

    return JsonResponse({
        'status': 'ok',
        'has_transaction': True,
        'transaction_number': latest_txn.transaction_number,
        'amount': str(latest_txn.amount),
        'balance_after': str(latest_txn.balance_after),
        'created_at': latest_txn.created_at.isoformat(),
        'payment_method': latest_txn.payment_method,
        'account_balance': str(
            SusuAccount.objects.filter(customer=customer, status='ACTIVE').aggregate(
                t=Sum('current_balance')
            )['t'] or '0.00'
        ),
    })


@csrf_exempt
@require_POST
def paystack_webhook(request):
    """Paystack webhook for payment confirmations."""
    import logging
    logger = logging.getLogger('apps.payments')

    from .paystack import verify_webhook_signature

    signature = request.headers.get('x-paystack-signature', '')
    if not verify_webhook_signature(request.body, signature):
        logger.warning("Webhook signature verification failed")
        return HttpResponse(status=400)

    import json
    try:
        payload = json.loads(request.body)
    except Exception:
        logger.warning("Webhook invalid JSON")
        return HttpResponse(status=400)

    event = payload.get('event')
    if event != 'charge.success':
        return HttpResponse(status=200)

    data = payload.get('data', {})
    reference = data.get('reference')
    if not reference:
        logger.warning("Webhook missing reference")
        return HttpResponse(status=200)

    existing = Transaction.objects.filter(idempotency_key=reference).first()
    if existing:
        logger.info("Webhook duplicate — txn %s already exists", existing.transaction_number)
        return HttpResponse(status=200)

    from .paystack import verify_payment
    result = verify_payment(reference)
    if not result['status'] or result.get('amount', 0) <= 0:
        logger.warning("Webhook verify_payment failed for ref=%s", reference)
        return HttpResponse(status=200)

    try:
        metadata = data.get('metadata', {})
        account_id = metadata.get('account_id')
        customer_id = metadata.get('customer_id')
        if not account_id or not customer_id:
            logger.warning("Webhook missing metadata. ref=%s", reference)
            return HttpResponse(status=200)
        from apps.customers.models import Customer
        customer = Customer.objects.get(pk=customer_id)
        account = SusuAccount.objects.get(pk=account_id, customer=customer, status='ACTIVE')
    except (TypeError, ValueError, Customer.DoesNotExist, SusuAccount.DoesNotExist) as e:
        logger.error("Webhook lookup failed: %s ref=%s", e, reference)
        return HttpResponse(status=200)

    txn, success, error = record_contribution(
        customer=customer,
        amount=result['amount'],
        payment_method=Transaction.PaymentMethod.PAYSTACK,
        created_by=None,
        reference=reference,
        notes=f'Webhook contribution via Paystack. Ref: {reference}',
        account=account,
    )

    if success:
        txn.idempotency_key = reference
        txn.save(update_fields=['idempotency_key'])
        logger.info(
            "Webhook contribution recorded. txn=%s amount=%.2f customer=%s",
            txn.transaction_number, float(result['amount']), customer.pk,
        )
        try:
            from apps.notifications.tasks import send_contribution_sms
            send_contribution_sms.delay(txn.pk)
        except Exception:
            pass
    else:
        logger.error("Webhook record_contribution failed: %s ref=%s", error, reference)

    return HttpResponse(status=200)
