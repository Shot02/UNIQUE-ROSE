from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout
from django.db.models import Q, Sum, F, Count, Subquery, OuterRef
from django.utils import timezone
from datetime import datetime, timedelta
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
import json
import uuid
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from decimal import Decimal, InvalidOperation

from .models import (
    User, Product, Sale, SaleItem, Payment, Category, 
    Supplier, StockMovement, PendingCart, SavedCart, RefundRequest, Refund, UserNotification
)



def to_decimal(value, default='0.00'):
    """Safely convert any value to Decimal with proper rounding"""
    from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
    
    try:
        if isinstance(value, Decimal):
            return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        elif value is None:
            return Decimal(default).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        else:
            # Convert to string first to avoid float precision issues
            return Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    
    
# =================== AUTHENTICATION VIEWS ===================
def login_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            login(request, user)
            if user.role == 'admin' or user.is_superuser:
                return redirect('admin_dashboard')
            else:
                return redirect('home')
        else:
            messages.error(request, 'Invalid username or password')
    
    return render(request, 'login.html')

@login_required
def logout_view(request):
    logout(request)
    return redirect('login')

# =================== HOME / POS VIEWS ===================
@login_required
def home(request):
    products = Product.objects.filter(quantity__gt=0).order_by('name')
    categories = Category.objects.all()
    
    pending_cart = PendingCart.objects.filter(staff=request.user).first()
    
    context = {
        'products': products,
        'categories': categories,
        'pending_cart': pending_cart.cart_data if pending_cart else None,
        'now': timezone.now()
    }
    return render(request, 'home.html', context)

@login_required
def search_products_api(request):
    """API endpoint for real-time products search"""
    search_term = request.GET.get('q', '').strip()
    
    products = Product.objects.all().select_related('category', 'supplier').order_by('name')
    
    if search_term:
        products = products.filter(
            Q(name__icontains=search_term) |
            Q(sku__icontains=search_term) |
            Q(category__name__icontains=search_term) |
            Q(supplier__name__icontains=search_term)
        )[:50]
    
    # Serialize results
    results = []
    for product in products:
        results.append({
            'id': product.id,
            'name': product.name,
            'sku': product.sku,
            'category': product.category.name if product.category else 'N/A',
            'category_id': product.category.id if product.category else None,
            'supplier': product.supplier.name if product.supplier else 'N/A',
            'supplier_id': product.supplier.id if product.supplier else None,
            'description': product.description or '',
            'price': float(product.price),
            'cost_price': float(product.cost_price),
            'quantity': product.quantity,
            'reorder_level': product.reorder_level,
            'image': product.image.url if product.image else '',
            'is_low_stock': product.is_low_stock,
            'limited': products.count() >= 50
        })
    
    return JsonResponse({
        'success': True,
        'results': results,
        'count': len(results),
        'limited': len(results) >= 50
    })

@login_required
@csrf_exempt
def process_sale(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            # Import Decimal here
            from decimal import Decimal, ROUND_HALF_UP
            
            # Get saved cart ID if exists
            saved_cart_id = data.get('saved_cart_id')
            saved_cart = None
            
            if saved_cart_id:
                try:
                    saved_cart = SavedCart.objects.get(id=saved_cart_id, staff=request.user)
                except SavedCart.DoesNotExist:
                    saved_cart = None
            
            # Validate data
            if not data.get('items'):
                return JsonResponse({'success': False, 'error': 'No items in cart'})
            
            # Validate customer name (required)
            customer_name = data.get('customer_name', '').strip()
            if not customer_name:
                return JsonResponse({'success': False, 'error': 'Customer name is required'})
            
            # Calculate with Decimal for precision
            subtotal = Decimal('0')
            for item in data['items']:
                item_price = Decimal(str(item['price'])).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                item_quantity = Decimal(str(item['quantity']))
                item_discount = Decimal(str(item.get('discount', 0))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                subtotal += (item_price * item_quantity) - item_discount
            
            # Calculate total discount
            item_discounts_total = Decimal('0')
            for item in data['items']:
                item_discounts_total += Decimal(str(item.get('discount', 0))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            
            if 'discount' in data:
                sale_discount = Decimal(str(data.get('discount', 0))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            else:
                sale_discount = item_discounts_total
            
            total = (subtotal - sale_discount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

            amount_paid = Decimal(str(data.get('amount_paid', 0))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            balance = (total - amount_paid).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            
            # Ensure balance is not negative
            if balance < Decimal('0'):
                balance = Decimal('0')
            
            # Validate stock before processing
            for item in data['items']:
                try:
                    product = Product.objects.get(id=item['product_id'])
                    if product.quantity < item['quantity']:
                        return JsonResponse({
                            'success': False, 
                            'error': f'Insufficient stock for {product.name}. Available: {product.quantity}, Requested: {item["quantity"]}'
                        })
                except Product.DoesNotExist:
                    return JsonResponse({'success': False, 'error': f'Product ID {item["product_id"]} not found'})
            
            # Generate invoice number
            today_str = timezone.now().strftime('%Y%m%d')
            invoice_number = f"INV-{today_str}-{uuid.uuid4().hex[:6].upper()}"
            
            # Determine payment status
            if balance <= Decimal('0'):
                payment_status = 'paid'
            elif balance < total:
                payment_status = 'partial'
            else:
                payment_status = 'unpaid'
            
            # Create sale with Decimal values
            sale = Sale.objects.create(
                invoice_number=invoice_number,
                staff=request.user,
                customer_name=customer_name,
                customer_phone=data.get('customer_phone', '').strip(),  # Optional
                subtotal=subtotal,
                discount=sale_discount,
                total=total,
                amount_paid=amount_paid,
                balance=balance,
                payment_status=payment_status
            )
            
            # Create sale items with Decimal values
            for item in data.get('items', []):
                product = Product.objects.get(id=item['product_id'])
                
                item_price = Decimal(str(item['price'])).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                item_quantity = int(item['quantity'])
                item_discount = Decimal(str(item.get('discount', 0))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                item_total = (item_price * Decimal(str(item_quantity))) - item_discount
                
                SaleItem.objects.create(
                    sale=sale,
                    product=product,
                    product_name=product.name,
                    quantity=item_quantity,
                    price=item_price,
                    discount=item_discount,
                    total=item_total
                )
                
                # Update product quantity
                product.quantity -= item_quantity
                product.save()
                
                # Create stock movement record
                StockMovement.objects.create(
                    product=product,
                    movement_type='out',
                    quantity=item_quantity,
                    reference=invoice_number,
                    notes=f"Sold in invoice {invoice_number}",
                    created_by=request.user
                )
            
            # Create payment record if payment made
            if amount_paid > Decimal('0'):
                Payment.objects.create(
                    sale=sale,
                    amount=amount_paid,
                    payment_method=data.get('payment_method', 'cash'),
                    reference=data.get('reference', ''),
                    notes=data.get('notes', ''),
                    created_by=request.user
                )
            
            # Clear pending cart
            PendingCart.objects.filter(staff=request.user).delete()
            
            # Delete saved cart if it was loaded
            if saved_cart:
                saved_cart.delete()
                cart_deleted = True
            else:
                cart_deleted = False
            
            # Create notifications
            UserNotification.create_notification(
                user=request.user,
                notification_type='sales',
                message=f'New sale: {invoice_number} - ₦{total:,.2f}',
                related_id=sale.id
            )
            
            admins = User.objects.filter(Q(role='admin') | Q(is_superuser=True))
            for admin in admins.distinct():
                if admin != request.user:
                    UserNotification.create_notification(
                        user=admin,
                        notification_type='dashboard',
                        message=f'New sale by {request.user.username}: {invoice_number}',
                        related_id=sale.id
                    )
            
            return JsonResponse({
                'success': True,
                'sale_id': sale.id,
                'invoice_number': invoice_number,
                'total': float(total),
                'balance': float(balance),
                'cart_deleted': cart_deleted,
                'cart_id': saved_cart_id if saved_cart else None
            })
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})

@login_required
def view_receipt(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id)
    items = sale.items.all()
    payments = sale.payments.all()
    
    # Calculate total item discounts
    item_discounts_total = sum(item.discount for item in items)
    total_discount = item_discounts_total + sale.discount
    
    # Get payment method from latest payment
    payment_method = payments.last().payment_method if payments.exists() else 'cash'
    
    context = {
        'sale': sale,
        'items': items,
        'payments': payments,
        'payment_method': payment_method,
        'item_discounts_total': item_discounts_total,
        'total_discount': total_discount,  # Total of all discounts
    }
    return render(request, 'receipt.html', context)

# =================== DASHBOARD VIEWS ===================
@login_required
def admin_dashboard(request):
    # Get date filter from request
    date_filter = request.GET.get('date_filter', 'today')
    
    # Calculate date range correctly
    today = timezone.now().date()
    
    if date_filter == 'today':
        start_date = today
        end_date = today + timedelta(days=1)  # Include today fully
    elif date_filter == 'week':
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=7)
    elif date_filter == 'month':
        start_date = today.replace(day=1)
        # Get last day of month
        if start_date.month == 12:
            end_date = start_date.replace(year=start_date.year + 1, month=1, day=1)
        else:
            end_date = start_date.replace(month=start_date.month + 1, day=1)
    elif date_filter == 'year':
        start_date = today.replace(month=1, day=1)
        end_date = today.replace(year=today.year + 1, month=1, day=1)
    else:
        custom_start = request.GET.get('custom_start')
        custom_end = request.GET.get('custom_end')
        if custom_start and custom_end:
            try:
                start_date = datetime.strptime(custom_start, '%Y-%m-%d').date()
                end_date = datetime.strptime(custom_end, '%Y-%m-%d').date() + timedelta(days=1)
            except ValueError:
                start_date = today
                end_date = today + timedelta(days=1)
        else:
            start_date = today
            end_date = today + timedelta(days=1)
    
    # Statistics
    total_products = Product.objects.count()
    
    # Total sales count (only completed sales)
    total_sales = Sale.objects.filter(
        created_at__range=[start_date, end_date]
    ).count()
    
    # Low stock products
    low_stock_products = Product.objects.filter(
        quantity__lte=F('reorder_level'),
        quantity__gt=0
    ).count()
    
    # Debtors count (balance > 0) within date range
    debtors_count = Sale.objects.filter(
        balance__gt=0,
        created_at__range=[start_date, end_date]
    ).count()
    
    # Payment statistics - INCLUDE REFUNDS (negative payments)
    cash_payments = Payment.objects.filter(
        payment_method='cash',
        created_at__range=[start_date, end_date]
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    transfer_payments = Payment.objects.filter(
        payment_method='transfer',
        created_at__range=[start_date, end_date]
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    card_payments = Payment.objects.filter(
        payment_method='card',
        created_at__range=[start_date, end_date]
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    # Total refunds (negative payments with payment_method='refund')
    total_refunds = Payment.objects.filter(
        payment_method='refund',
        created_at__range=[start_date, end_date]
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    # Total revenue - ACTUAL revenue after refunds
    # Sum all payments (including negative refunds)
    total_payments = Payment.objects.filter(
        created_at__range=[start_date, end_date]
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    # Make sure revenue is not negative
    total_revenue = max(total_payments, Decimal('0.00'))
    
    # ========== PROFIT CALCULATIONS ==========
    # Get all sales in the date range with their items
    sales_in_range = Sale.objects.filter(
        created_at__range=[start_date, end_date]
    ).prefetch_related('items__product')
    
    total_revenue_from_sales = Decimal('0.00')
    total_cost_of_goods = Decimal('0.00')
    total_profit = Decimal('0.00')
    profit_margin_percentage = Decimal('0.00')
    
    for sale in sales_in_range:
        sale_revenue = Decimal('0.00')
        sale_cost = Decimal('0.00')
        
        for item in sale.items.all():
            if item.product:  # Make sure product still exists
                # Revenue from this item (after discounts)
                item_revenue = item.total
                # Cost of this item
                item_cost = item.product.cost_price * Decimal(str(item.quantity))
                
                sale_revenue += item_revenue
                sale_cost += item_cost
            else:
                # If product is deleted, use the stored price as revenue
                # but we can't calculate cost accurately
                sale_revenue += item.total
        
        total_revenue_from_sales += sale_revenue
        total_cost_of_goods += sale_cost
    
    # Calculate profit
    total_profit = total_revenue_from_sales - total_cost_of_goods
    
    # Calculate profit margin percentage (avoid division by zero)
    if total_revenue_from_sales > 0:
        profit_margin_percentage = (total_profit / total_revenue_from_sales) * 100
    else:
        profit_margin_percentage = Decimal('0.00')
    
    # Alternative profit calculation using SaleItem directly (more accurate)
    # This method doesn't require looping through sales
    sale_items_in_range = SaleItem.objects.filter(
        sale__created_at__range=[start_date, end_date]
    ).select_related('product')
    
    alt_total_revenue = Decimal('0.00')
    alt_total_cost = Decimal('0.00')
    
    for item in sale_items_in_range:
        alt_total_revenue += item.total
        if item.product:
            alt_total_cost += item.product.cost_price * Decimal(str(item.quantity))
    
    alt_total_profit = alt_total_revenue - alt_total_cost
    
    # Calculate profit by payment method
    profit_by_payment = {
        'cash': Decimal('0.00'),
        'transfer': Decimal('0.00'),
        'card': Decimal('0.00'),
    }
    
    # Get all payments with their associated sale items
    payments_in_range = Payment.objects.filter(
        created_at__range=[start_date, end_date],
        payment_method__in=['cash', 'transfer', 'card']
    ).select_related('sale')
    
    for payment in payments_in_range:
        if payment.sale:
            # Calculate profit proportion for this payment
            sale_total = payment.sale.total
            if sale_total > 0:
                payment_ratio = abs(payment.amount) / sale_total
                
                # Get all items for this sale
                sale_items = payment.sale.items.all().select_related('product')
                payment_profit = Decimal('0.00')
                
                for item in sale_items:
                    item_revenue = item.total * payment_ratio
                    if item.product:
                        item_cost = item.product.cost_price * Decimal(str(item.quantity)) * payment_ratio
                        payment_profit += item_revenue - item_cost
                
                profit_by_payment[payment.payment_method] += payment_profit
    
    # Get top 5 most profitable products
    top_profitable_products = []
    product_profit_map = {}
    
    for item in sale_items_in_range:
        if item.product:
            product_id = item.product.id
            if product_id not in product_profit_map:
                product_profit_map[product_id] = {
                    'name': item.product.name,
                    'sku': item.product.sku,
                    'revenue': Decimal('0.00'),
                    'cost': Decimal('0.00'),
                    'quantity_sold': 0,
                    'profit': Decimal('0.00')
                }
            
            product_profit_map[product_id]['revenue'] += item.total
            product_profit_map[product_id]['cost'] += item.product.cost_price * Decimal(str(item.quantity))
            product_profit_map[product_id]['quantity_sold'] += item.quantity
    
    # Calculate profit for each product and sort
    for product_id, data in product_profit_map.items():
        data['profit'] = data['revenue'] - data['cost']
        if data['profit'] > 0:
            top_profitable_products.append(data)
    
    # Sort by profit descending and take top 5
    top_profitable_products = sorted(
        top_profitable_products, 
        key=lambda x: x['profit'], 
        reverse=True
    )[:5]
    
    # Recent sales with search and limit
    recent_sales = Sale.objects.filter(
        created_at__range=[start_date, end_date]
    ).select_related('staff').order_by('-created_at')
    
    sales_search = request.GET.get('sales_search', '')
    if sales_search:
        recent_sales = recent_sales.filter(
            Q(invoice_number__icontains=sales_search) |
            Q(customer_name__icontains=sales_search) |
            Q(staff__username__icontains=sales_search) |
            Q(customer_phone__icontains=sales_search)
        )[:50]
    else:
        recent_sales = recent_sales[:50]
    
    # Low stock items with search and limit
    low_stock = Product.objects.filter(
        quantity__lte=F('reorder_level'),
        quantity__gt=0
    ).select_related('category').order_by('quantity')
    
    stock_search = request.GET.get('stock_search', '')
    if stock_search:
        low_stock = low_stock.filter(
            Q(name__icontains=stock_search) |
            Q(sku__icontains=stock_search) |
            Q(category__name__icontains=stock_search)
        )[:50]
    else:
        low_stock = low_stock[:50]
    
    # Pending refund requests count
    pending_refunds = RefundRequest.objects.filter(status='pending').count()
    
    # Today's refunds
    today_refunds = Refund.objects.filter(
        processed_date__date=today
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    context = {
        'date_filter': date_filter,
        'start_date': start_date,
        'end_date': end_date - timedelta(days=1),  # Adjust for display
        'today': today,
        'total_products': total_products,
        'total_sales': total_sales,
        'low_stock_products': low_stock_products,
        'debtors_count': debtors_count,
        'cash_payments': cash_payments,
        'transfer_payments': transfer_payments,
        'card_payments': card_payments,
        'total_refunds': abs(total_refunds),  # Absolute value for display
        'total_revenue': total_revenue,
        # Profit metrics
        'total_profit': total_profit,
        'profit_margin': profit_margin_percentage,
        'total_cost_of_goods': total_cost_of_goods,
        'total_revenue_from_sales': total_revenue_from_sales,
        'alt_total_profit': alt_total_profit,
        'profit_by_payment': profit_by_payment,
        'top_profitable_products': top_profitable_products,
        # Existing data
        'recent_sales': recent_sales,
        'low_stock': low_stock,
        'sales_search': sales_search,
        'stock_search': stock_search,
        'pending_refunds': pending_refunds,
        'today_refunds': today_refunds,
    }
    
    # Mark dashboard notifications as read
    if request.user.is_authenticated:
        UserNotification.mark_as_read(request.user, 'dashboard')
    
    return render(request, 'admin_dashboard.html', context)


@login_required
def profit_stats_api(request):
    """API endpoint to get profit statistics for dashboard"""
    try:
        # Get date range from request
        date_filter = request.GET.get('date_filter', 'today')
        today = timezone.now().date()
        
        # Calculate date range
        if date_filter == 'today':
            start_date = today
            end_date = today + timedelta(days=1)
        elif date_filter == 'week':
            start_date = today - timedelta(days=today.weekday())
            end_date = start_date + timedelta(days=7)
        elif date_filter == 'month':
            start_date = today.replace(day=1)
            if start_date.month == 12:
                end_date = start_date.replace(year=start_date.year + 1, month=1, day=1)
            else:
                end_date = start_date.replace(month=start_date.month + 1, day=1)
        elif date_filter == 'year':
            start_date = today.replace(month=1, day=1)
            end_date = today.replace(year=today.year + 1, month=1, day=1)
        else:
            custom_start = request.GET.get('custom_start')
            custom_end = request.GET.get('custom_end')
            if custom_start and custom_end:
                start_date = datetime.strptime(custom_start, '%Y-%m-%d').date()
                end_date = datetime.strptime(custom_end, '%Y-%m-%d').date() + timedelta(days=1)
            else:
                start_date = today
                end_date = today + timedelta(days=1)
        
        # Get all sale items in range with their products
        sale_items = SaleItem.objects.filter(
            sale__created_at__range=[start_date, end_date]
        ).select_related('product')
        
        total_revenue = Decimal('0.00')
        total_cost = Decimal('0.00')
        
        # Daily profit data for chart
        daily_profit = {}
        
        for item in sale_items:
            # Get sale date for grouping
            sale_date = item.sale.created_at.date()
            date_str = sale_date.isoformat()
            
            if date_str not in daily_profit:
                daily_profit[date_str] = {
                    'date': date_str,
                    'revenue': Decimal('0.00'),
                    'cost': Decimal('0.00'),
                    'profit': Decimal('0.00'),
                    'items_sold': 0
                }
            
            # Add to totals
            total_revenue += item.total
            daily_profit[date_str]['revenue'] += item.total
            daily_profit[date_str]['items_sold'] += item.quantity
            
            if item.product:
                item_cost = item.product.cost_price * Decimal(str(item.quantity))
                total_cost += item_cost
                daily_profit[date_str]['cost'] += item_cost
        
        # Calculate profit
        total_profit = total_revenue - total_cost
        
        # Calculate daily profit
        for date, data in daily_profit.items():
            data['profit'] = data['revenue'] - data['cost']
            # Convert to float for JSON
            data['revenue'] = float(data['revenue'])
            data['cost'] = float(data['cost'])
            data['profit'] = float(data['profit'])
        
        # Sort daily profit by date
        daily_profit_list = sorted(daily_profit.values(), key=lambda x: x['date'])
        
        # Calculate profit margin
        profit_margin = 0
        if total_revenue > 0:
            profit_margin = float((total_profit / total_revenue) * 100)
        
        return JsonResponse({
            'success': True,
            'total_revenue': float(total_revenue),
            'total_cost': float(total_cost),
            'total_profit': float(total_profit),
            'profit_margin': profit_margin,
            'daily_profit': daily_profit_list,
            'items_sold_count': sale_items.count(),
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({
            'success': False,
            'error': str(e)
        })


# =================== PRODUCT VIEWS ===================
@login_required
def product_list(request):
    products = Product.objects.all().select_related('category', 'supplier').order_by('-created_at')
    
    search_query = request.GET.get('search', '').strip()
    if search_query:
        products = products.filter(
            Q(name__icontains=search_query) |
            Q(sku__icontains=search_query) |
            Q(category__name__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(supplier__name__icontains=search_query)
        )[:50]
    
    # Check if it's an AJAX request
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        # Return only the table body for AJAX requests
        html = render_to_string('partials/product_table_body.html', {
            'products': products,
            'search_query': search_query,
        })
        return HttpResponse(html)
    
    categories = Category.objects.all()
    suppliers = Supplier.objects.all()
    
    context = {
        'products': products,
        'search_query': search_query,
        'categories': categories,
        'suppliers': suppliers,
    }
    return render(request, 'product_list.html', context)

@login_required
def add_product(request):
    if request.method == 'POST':
        try:
            name = request.POST.get('name', 'Unnamed Product')
            category_id = request.POST.get('category')
            supplier_id = request.POST.get('supplier')
            description = request.POST.get('description', '')
            price = request.POST.get('price')
            cost_price = request.POST.get('cost_price')
            quantity = request.POST.get('quantity')
            reorder_level = request.POST.get('reorder_level')
            image = request.FILES.get('image')


            category = None
            if category_id:
                try:
                    category = Category.objects.get(id=category_id)
                except (Category.DoesNotExist, ValueError):
                    pass
            
            supplier = None
            if supplier_id:
                try:
                    supplier = Supplier.objects.get(id=supplier_id)
                except (Supplier.DoesNotExist, ValueError):
                    pass
            
            try:
                price_decimal = Decimal(price) if price else Decimal('0.00')
            except (InvalidOperation, TypeError, ValueError):
                price_decimal = Decimal('0.00')
            
            try:
                cost_price_decimal = Decimal(cost_price) if cost_price else Decimal('0.00')
            except (InvalidOperation, TypeError, ValueError):
                cost_price_decimal = Decimal('0.00')
            
            try:
                quantity_int = int(quantity) if quantity else 0
            except ValueError:
                quantity_int = 0
            
            try:
                reorder_level_int = int(reorder_level) if reorder_level else 10
            except ValueError:
                reorder_level_int = 10
            
            # Create product with all fields - ALL optional
            product = Product.objects.create(
                name=name,
                category=category,
                supplier=supplier,
                description=description,
                price=price_decimal,
                cost_price=cost_price_decimal,
                quantity=quantity_int,
                reorder_level=reorder_level_int,
            )
            
            # Handle image if provided
            if image:
                product.image = image
                product.save()
            
            messages.success(request, f'Product "{name}" added successfully!')
            return redirect('product_list')
            
        except Exception as e:
            messages.error(request, f'Error adding product: {str(e)}')
            return redirect('add_product')
    
    categories = Category.objects.all()
    suppliers = Supplier.objects.all()
    
    context = {
        'categories': categories,
        'suppliers': suppliers,
    }
    return render(request, 'product_form.html', context)

@login_required
def edit_product(request, pk):
    product = get_object_or_404(Product, id=pk)
    
    if request.method == 'POST':
        try:
            # Update basic fields
            product.name = request.POST.get('name', 'Unnamed Product')
            product.description = request.POST.get('description', '')
            
            # Handle category
            category_id = request.POST.get('category')
            new_category = request.POST.get('new_category')
            
            if new_category:
                category, created = Category.objects.get_or_create(name=new_category)
                product.category = category
            elif category_id:
                try:
                    product.category = Category.objects.get(id=category_id)
                except (Category.DoesNotExist, ValueError):
                    product.category = None
            else:
                product.category = None
            
            # Handle supplier
            supplier_id = request.POST.get('supplier')
            new_supplier = request.POST.get('new_supplier')
            
            if new_supplier:
                supplier, created = Supplier.objects.get_or_create(name=new_supplier)
                product.supplier = supplier
            elif supplier_id:
                try:
                    product.supplier = Supplier.objects.get(id=supplier_id)
                except (Supplier.DoesNotExist, ValueError):
                    product.supplier = None
            else:
                product.supplier = None
            
            # Update numeric fields with safe defaults
            try:
                price_val = request.POST.get('price')
                product.price = Decimal(price_val) if price_val else Decimal('0.00')
            except (InvalidOperation, TypeError, ValueError):
                product.price = Decimal('0.00')
            
            try:
                cost_price_val = request.POST.get('cost_price')
                product.cost_price = Decimal(cost_price_val) if cost_price_val else Decimal('0.00')
            except (InvalidOperation, TypeError, ValueError):
                product.cost_price = Decimal('0.00')
            
            try:
                quantity_val = request.POST.get('quantity')
                product.quantity = int(quantity_val) if quantity_val else 0
            except ValueError:
                product.quantity = 0
            
            try:
                reorder_val = request.POST.get('reorder_level')
                product.reorder_level = int(reorder_val) if reorder_val else 10
            except ValueError:
                product.reorder_level = 10
            
            # Handle image upload
            if 'image' in request.FILES:
                # Delete old image if exists
                if product.image:
                    product.image.delete(save=False)
                product.image = request.FILES['image']
            
            # Clear image if requested
            if request.POST.get('clear_image') == '1':
                if product.image:
                    product.image.delete(save=False)
                product.image = None
            
            product.save()
            
            messages.success(request, f'Product "{product.name}" updated successfully!')
            
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': True})
            else:
                return redirect('product_list')
            
        except Exception as e:
            error_msg = f'Error updating product: {str(e)}'
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': error_msg})
            else:
                messages.error(request, error_msg)
                return redirect('edit_product', pk=pk)
    
    # Regular request - render template
    categories = Category.objects.all()
    suppliers = Supplier.objects.all()
    
    context = {
        'product': product,
        'categories': categories,
        'suppliers': suppliers,
        'action': 'Edit',
    }
    return render(request, 'product_form.html', context)

@login_required
def delete_product(request, pk):
    product = get_object_or_404(Product, id=pk)
    
    if request.method == 'POST':
        product_name = product.name
        # Delete image file if exists
        if product.image:
            product.image.delete(save=False)
        product.delete()
        messages.success(request, f'Product "{product_name}" deleted successfully!')
        return redirect('product_list')
    
    return render(request, 'product_confirm_delete.html', {'product': product})

# =================== DEBTORS VIEWS ===================
@login_required
def debtors_list(request):
    """List of REAL debtors - excludes cases where refunds were processed"""
    from decimal import Decimal
    
    # Get all sales with balance > 0
    all_sales = Sale.objects.filter(balance__gt=0).select_related('staff').prefetch_related('payments').order_by('-created_at')
    
    real_debtors = []
    for sale in all_sales:
        non_refund_payments = sale.payments.filter(
            ~Q(payment_method='refund')
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        refunds = sale.payments.filter(
            payment_method='refund'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        net_paid = non_refund_payments + refunds 
        
        if net_paid < sale.total:
            real_debtors.append(sale)
    
    search_query = request.GET.get('search', '')
    if search_query:
        # Filter within real debtors
        filtered_debtors = []
        for debtor in real_debtors:
            if (search_query.lower() in debtor.invoice_number.lower() or
                search_query.lower() in (debtor.customer_name or '').lower() or
                search_query.lower() in (debtor.customer_phone or '').lower() or
                search_query.lower() in debtor.staff.username.lower()):
                filtered_debtors.append(debtor)
        real_debtors = filtered_debtors[:50]  # Limit results
    
    context = {
        'debtors': real_debtors,
        'search_query': search_query,
    }
    return render(request, 'debtors_list.html', context)



@login_required
def record_payment(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id)
    
    if request.method == 'POST':
        try:
            amount = Decimal(request.POST.get('amount', 0))
            payment_method = request.POST.get('payment_method', 'cash')
            reference = request.POST.get('reference', '')
            notes = request.POST.get('notes', '')
            
            if amount <= 0:
                messages.error(request, 'Amount must be greater than 0')
                return redirect('record_payment', sale_id=sale_id)
            
            if amount > sale.balance:
                messages.error(request, f'Amount cannot exceed balance of ₦{sale.balance:,.2f}')
                return redirect('record_payment', sale_id=sale_id)
            
            # Create payment
            Payment.objects.create(
                sale=sale,
                amount=amount,
                payment_method=payment_method,
                reference=reference,
                notes=notes,
                created_by=request.user
            )
            
            # Update sale
            sale.amount_paid += amount
            sale.balance = sale.total - sale.amount_paid
            
            if sale.balance <= 0:
                sale.payment_status = 'paid'
            else:
                sale.payment_status = 'partial'
            
            sale.save()
            
            # Create debtor notification for admin users
            if sale.balance > 0:  # Still has balance after payment
                UserNotification.create_notification(
                    user=request.user,
                    notification_type='debtors',
                    message=f'Partial payment on {sale.invoice_number} - Balance: ₦{sale.balance:,.2f}',
                    related_id=sale.id
                )
            else:  # Fully paid
                # Mark debtor notifications as read since debt is cleared
                UserNotification.mark_as_read(request.user, 'debtors')
            
            # Create dashboard notification for admin users
            # FIXED: Use the custom User model imported at the top
            admins = User.objects.filter(Q(role='admin') | Q(is_superuser=True))
            for admin in admins.distinct():
                if admin != request.user:
                    UserNotification.create_notification(
                        user=admin,
                        notification_type='dashboard',
                        message=f'Payment recorded by {request.user.username} on {sale.invoice_number}',
                        related_id=sale.id
                    )
            
            messages.success(request, f'Payment of ₦{amount:,.2f} recorded successfully!')
            return redirect('debtors_list')
            
        except Exception as e:
            messages.error(request, f'Error recording payment: {str(e)}')
    
    context = {
        'sale': sale,
    }
    return render(request, 'record_payment.html', context)

# =================== CART VIEWS ===================
@login_required
@csrf_exempt
def save_pending_cart(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            # Validate cart data
            if not data.get('items'):
                return JsonResponse({'success': False, 'error': 'Cart is empty'})
            
            # Calculate totals
            subtotal = Decimal('0')
            for item in data['items']:
                item_price = Decimal(str(item.get('price', 0)))
                item_quantity = Decimal(str(item.get('quantity', 1)))
                item_discount = Decimal(str(item.get('discount', 0)))
                subtotal += (item_price * item_quantity) - item_discount
            
            cart_data = {
                'items': data['items'],
                'customer_name': data.get('customer_name', ''),
                'customer_phone': data.get('customer_phone', ''),
                'payment_type': data.get('payment_type', 'full'),
                'payment_method': data.get('payment_method', 'cash'),
                'amount_paid': float(data.get('amount_paid', 0)),
                'subtotal': float(subtotal),
                'total': float(subtotal),
                'timestamp': timezone.now().isoformat()
            }
            
            # Delete existing pending cart
            PendingCart.objects.filter(staff=request.user).delete()
            
            # Create new pending cart
            PendingCart.objects.create(
                staff=request.user,
                cart_data=cart_data
            )
            
            return JsonResponse({'success': True})
            
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})

@login_required
def load_pending_cart(request):
    try:
        pending_cart = PendingCart.objects.filter(staff=request.user).first()
        
        if pending_cart:
            return JsonResponse({
                'success': True,
                'cart_data': pending_cart.cart_data
            })
        else:
            return JsonResponse({
                'success': True,
                'cart_data': None
            })
            
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
@csrf_exempt
def delete_pending_cart(request):
    if request.method == 'POST':
        try:
            PendingCart.objects.filter(staff=request.user).delete()
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})

@login_required
def saved_carts_list(request):
    saved_carts = SavedCart.objects.filter(staff=request.user).order_by('-created_at')
    
    context = {
        'saved_carts': saved_carts,
    }
    return render(request, 'saved_carts_list.html', context)

@login_required
@csrf_exempt
def save_cart(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            cart_name = data.get('cart_name', f'Cart {timezone.now().strftime("%Y-%m-%d %H:%M")}')
            
            # Validate cart data
            cart_data = data.get('cart_data', {})
            if not cart_data.get('items'):
                return JsonResponse({'success': False, 'error': 'Cart is empty'})
            
            # Calculate totals if not provided
            if 'subtotal' not in cart_data:
                subtotal = Decimal('0')
                for item in cart_data['items']:
                    item_price = Decimal(str(item.get('price', 0)))
                    item_quantity = Decimal(str(item.get('quantity', 1)))
                    item_discount = Decimal(str(item.get('discount', 0)))
                    subtotal += (item_price * item_quantity) - item_discount
                cart_data['subtotal'] = float(subtotal)
                cart_data['total'] = float(subtotal)
            
            # Save cart
            saved_cart = SavedCart.objects.create(
                staff=request.user,
                cart_name=cart_name,
                cart_data=cart_data
            )
            
            return JsonResponse({
                'success': True,
                'cart_id': saved_cart.id,
                'cart_name': saved_cart.cart_name
            })
            
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})

@login_required
def load_saved_cart(request, cart_id):
    try:
        saved_cart = SavedCart.objects.get(id=cart_id, staff=request.user)
        
        return JsonResponse({
            'success': True,
            'cart_data': saved_cart.cart_data,
            'cart_name': saved_cart.cart_name
        })
        
    except SavedCart.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Cart not found'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
@csrf_exempt
def delete_saved_cart(request, cart_id):
    if request.method == 'POST':
        try:
            saved_cart = SavedCart.objects.get(id=cart_id, staff=request.user)
            saved_cart.delete()
            
            return JsonResponse({'success': True})
        except SavedCart.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Cart not found'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})

@login_required
def view_saved_cart(request, cart_id):
    saved_cart = get_object_or_404(SavedCart, id=cart_id, staff=request.user)
    
    # Calculate totals for display
    cart_data = saved_cart.cart_data
    items_count = len(cart_data.get('items', []))
    total_amount = Decimal('0')
    for item in cart_data.get('items', []):
        item_price = Decimal(str(item.get('price', 0)))
        item_quantity = Decimal(str(item.get('quantity', 1)))
        item_discount = Decimal(str(item.get('discount', 0)))
        total_amount += (item_price * item_quantity) - item_discount
    
    context = {
        'saved_cart': saved_cart,
        'cart_data': cart_data,
        'items_count': items_count,
        'total_amount': total_amount,
    }
    return render(request, 'saved_cart_detail.html', context)

# =================== SALES HISTORY VIEWS ===================
@login_required
def sale_history(request):
    """Display all sales with pagination and real-time search"""
    search_query = request.GET.get('search', '')
    page_number = request.GET.get('page', 1)
    
    # Start with base queryset
    sales = Sale.objects.all().select_related('staff').order_by('-created_at')
    
    # Apply search filter if provided
    if search_query:
        sales = sales.filter(
            Q(invoice_number__icontains=search_query) |
            Q(customer_name__icontains=search_query) |
            Q(customer_phone__icontains=search_query) |
            Q(staff__username__icontains=search_query)
        )
    
    # Pagination - 50 per page
    paginator = Paginator(sales, 50)
    
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)
    
    # Calculate totals for the current page
    page_total = sum(float(sale.total) for sale in page_obj.object_list)
    
    context = {
        'page_obj': page_obj,
        'sales': page_obj.object_list,
        'search_query': search_query,
        'page_total': page_total,
        'total_sales_count': sales.count(),
    }
    
    return render(request, 'sale_history.html', context)

# =================== STAFF MANAGEMENT VIEWS ===================
@login_required
def register_staff(request):
    if not request.user.role == 'admin' and not request.user.is_superuser:
        messages.error(request, 'Only admins can register staff')
        return redirect('home')
    
    if request.method == 'POST':
        try:
            username = request.POST.get('username')
            email = request.POST.get('email')
            first_name = request.POST.get('first_name')
            last_name = request.POST.get('last_name')
            password = request.POST.get('password')
            role = request.POST.get('role', 'staff')
            phone = request.POST.get('phone', '')
            
            # Validate required fields
            if not username or not email or not password:
                messages.error(request, 'Username, email and password are required')
                return redirect('register_staff')
            
            # Check if username already exists
            if User.objects.filter(username=username).exists():
                messages.error(request, 'Username already exists')
                return redirect('register_staff')
            
            # Create user
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                role=role,
                phone=phone,
                is_staff=True
            )
            
            messages.success(request, f'Staff member "{username}" created successfully!')
            return redirect('staff_list')
            
        except Exception as e:
            messages.error(request, f'Error creating staff: {str(e)}')
    
    return render(request, 'register_staff.html')

@login_required
def staff_list(request):
    if not request.user.role == 'admin' and not request.user.is_superuser:
        messages.error(request, 'Only admins can view staff list')
        return redirect('home')
    
    staff = User.objects.filter(is_staff=True).order_by('-date_joined')
    
    search_query = request.GET.get('search', '')
    if search_query:
        staff = staff.filter(
            Q(username__icontains=search_query) |
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query) |
            Q(email__icontains=search_query) |
            Q(phone__icontains=search_query) |
            Q(role__icontains=search_query)
        )[:50]
    
    context = {
        'staff': staff,
        'search_query': search_query,
    }
    return render(request, 'staff_list.html', context)

@login_required
@csrf_exempt
def edit_staff(request):
    """Handle AJAX request to edit staff member - Alternative approach"""
    if not (request.user.role == 'admin' or request.user.is_superuser):
        return JsonResponse({'success': False, 'error': 'Only admins can edit staff'})
    
    if request.method == 'POST':
        try:
            user_id = request.POST.get('user_id')
            user = User.objects.get(id=user_id)
            
            # Update fields individually without triggering full save
            update_fields = []
            
            # Track which fields changed
            fields_to_update = ['username', 'email', 'first_name', 'last_name', 'phone', 'role', 'is_active']
            
            for field in fields_to_update:
                if field == 'role':
                    new_value = request.POST.get('role')
                    if new_value and new_value != user.role:
                        user.role = new_value
                        update_fields.append('role')
                elif field == 'is_active':
                    is_active = request.POST.get('is_active')
                    if is_active is not None:
                        new_value = is_active == 'true'
                        if new_value != user.is_active:
                            user.is_active = new_value
                            update_fields.append('is_active')
                else:
                    new_value = request.POST.get(field, getattr(user, field))
                    if new_value != getattr(user, field):
                        setattr(user, field, new_value)
                        update_fields.append(field)
            
            # Save only if fields changed
            if update_fields:
                user.save(update_fields=update_fields)
            
            # Handle password separately
            password = request.POST.get('password')
            if password and password.strip():
                user.set_password(password)
                user.save(update_fields=['password'])
                
                # Update session if changing own password
                if user == request.user:
                    from django.contrib.auth import update_session_auth_hash
                    update_session_auth_hash(request, user)
            
            return JsonResponse({'success': True})
            
        except Exception as e:
            import traceback
            print(f"Error editing staff: {str(e)}")
            print(traceback.format_exc())
            return JsonResponse({'success': False, 'error': str(e)})

@login_required
@csrf_exempt
def delete_staff(request, pk):
    if not request.user.role == 'admin' and not request.user.is_superuser:
        messages.error(request, 'Only admins can delete staff')
        return redirect('staff_list')
    
    if request.method == 'POST':
        try:
            staff_member = get_object_or_404(User, id=pk)
            
            # Don't allow deleting yourself
            if staff_member == request.user:
                messages.error(request, 'You cannot delete your own account')
                return redirect('staff_list')
            
            username = staff_member.username
            staff_member.delete()
            
            messages.success(request, f'Staff member "{username}" deleted successfully!')
            return redirect('staff_list')
            
        except Exception as e:
            messages.error(request, f'Error deleting staff: {str(e)}')
    
    return redirect('staff_list')


# =================== REFUND VIEWS ===================

@login_required
def refund_list(request):
    """List of processed refunds"""
    # Mark refund notifications as read when visiting this page
    if request.user.is_authenticated:
        UserNotification.mark_as_read(request.user, 'refunds')
    
    if request.user.role == 'admin' or request.user.is_superuser:
        refunds = Refund.objects.all().select_related(
            'sale', 'processed_by', 'refund_request', 'refund_request__created_by'
        ).order_by('-processed_date')
    else:
        refunds = Refund.objects.filter(
            refund_request__created_by=request.user
        ).select_related(
            'sale', 'processed_by', 'refund_request', 'refund_request__created_by'
        ).order_by('-processed_date')
    
    context = {
        'refunds': refunds,
    }
    return render(request, 'refund_list.html', context)

@login_required
def refund_requests_list(request):
    """List of all refund requests"""
    # Mark refund notifications as read when visiting this page
    if request.user.is_authenticated:
        UserNotification.mark_as_read(request.user, 'refunds')
    
    if request.user.role == 'admin' or request.user.is_superuser:
        refunds = RefundRequest.objects.all().select_related('sale', 'created_by', 'approved_by').order_by('-request_date')
    else:
        refunds = RefundRequest.objects.filter(created_by=request.user).select_related('sale', 'created_by', 'approved_by').order_by('-request_date')
    
    # Calculate statistics
    pending_count = refunds.filter(status='pending').count()
    approved_count = refunds.filter(status='approved').count()
    declined_count = refunds.filter(status='declined').count()
    total_count = refunds.count()
    
    context = {
        'refunds': refunds,
        'pending_count': pending_count,
        'approved_count': approved_count,
        'declined_count': declined_count,
        'total_count': total_count,
    }
    return render(request, 'refund_requests_list.html', context)

# Add this view to your views.py
@login_required
def refund_details_api(request, pk):
    """API endpoint to get refund details"""
    try:
        refund = RefundRequest.objects.get(id=pk)
        
        # Check if user can view this refund
        if not (refund.created_by == request.user or request.user.role == 'admin' or request.user.is_superuser):
            return JsonResponse({'success': False, 'error': 'Access denied'})
        
        refund_data = {
            'id': refund.id,
            'customer_name': refund.customer_name,
            'customer_phone': refund.customer_phone,
            'reason': refund.reason,
            'amount': float(refund.amount),
            'status': refund.status,
            'request_date': refund.request_date.isoformat(),
            'sale_invoice': refund.sale.invoice_number if refund.sale else None,
            'approved_by': refund.approved_by.get_full_name() if refund.approved_by else None,
            'approved_date': refund.approved_date.isoformat() if refund.approved_date else None,
            'created_by': refund.created_by.get_full_name() or refund.created_by.username,
        }
        
        return JsonResponse({'success': True, 'refund': refund_data})
        
    except RefundRequest.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Refund not found'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def create_refund_request(request):
    """Create new refund request with transaction selection - FIXED VERSION"""
    if request.method == 'POST':
        try:
            # Get form data
            customer_name = request.POST.get('customer_name', '').strip()
            customer_phone = request.POST.get('customer_phone', '').strip()
            reason = request.POST.get('reason', '').strip()
            sale_id = request.POST.get('sale_id')
            sale_item_id = request.POST.get('sale_item_id')
            amount = request.POST.get('amount', '0').strip()
            refund_method = request.POST.get('refund_method', '')
            
            print(f"DEBUG - Form data received:")
            print(f"  customer_name: {customer_name}")
            print(f"  customer_phone: {customer_phone}")
            print(f"  reason: {reason}")
            print(f"  sale_id: {sale_id}")
            print(f"  sale_item_id: {sale_item_id}")
            print(f"  amount: {amount}")
            print(f"  refund_method: {refund_method}")
            
            # Validate required fields
            if not reason or not amount:
                messages.error(request, 'Reason and amount are required')
                return redirect('create_refund_request')
            
            # Parse amount
            try:
                refund_amount = Decimal(amount)
                if refund_amount <= 0:
                    messages.error(request, 'Refund amount must be greater than 0')
                    return redirect('create_refund_request')
            except (InvalidOperation, ValueError):
                messages.error(request, 'Invalid refund amount')
                return redirect('create_refund_request')
            
            # Find or validate sale
            selected_sale = None
            selected_item = None
            
            if sale_id and sale_id != '':
                try:
                    selected_sale = Sale.objects.get(id=sale_id)
                    
                    # If sale item is specified
                    if sale_item_id and sale_item_id != '':
                        try:
                            selected_item = SaleItem.objects.get(id=sale_item_id, sale=selected_sale)
                            # Validate refund amount against item total
                            max_refund = selected_item.total
                            if refund_amount > max_refund:
                                messages.error(request, f'Refund amount cannot exceed item total (₦{max_refund:,.2f})')
                                return redirect('create_refund_request')
                        except SaleItem.DoesNotExist:
                            messages.error(request, 'Selected item not found')
                            return redirect('create_refund_request')
                    else:
                        # Validate against sale amount paid
                        max_refund = selected_sale.amount_paid
                        if refund_amount > max_refund:
                            messages.error(request, f'Refund amount cannot exceed paid amount (₦{max_refund:,.2f})')
                            return redirect('create_refund_request')
                    
                except Sale.DoesNotExist:
                    messages.error(request, 'Selected sale not found')
                    return redirect('create_refund_request')
            else:
                # If no sale selected, we need customer info
                if not customer_name or not customer_phone:
                    messages.error(request, 'Customer name and phone are required when no sale is selected')
                    return redirect('create_refund_request')
                
                # Find customer sales
                customer_sales = Sale.objects.filter(
                    Q(customer_name__iexact=customer_name) |
                    Q(customer_phone__iexact=customer_phone)
                ).order_by('-created_at')
                
                if not customer_sales.exists():
                    messages.error(request, f'No sales found for customer: {customer_name}')
                    return redirect('create_refund_request')
                
                # Use the most recent sale with sufficient paid amount
                for sale in customer_sales:
                    if sale.amount_paid >= refund_amount:
                        selected_sale = sale
                        break
                
                if not selected_sale:
                    messages.error(request, f'No sale found with sufficient paid amount for refund of ₦{refund_amount:,.2f}')
                    return redirect('create_refund_request')
            
            # Create refund request
            refund_request = RefundRequest.objects.create(
                customer_name=customer_name if customer_name else (selected_sale.customer_name or 'Unknown Customer'),
                customer_phone=customer_phone if customer_phone else (selected_sale.customer_phone or ''),
                reason=reason,
                amount=refund_amount,
                sale=selected_sale,
                sale_item=selected_item,
                created_by=request.user
            )
            
            # Set original amount
            if selected_item:
                refund_request.original_amount = selected_item.total
            elif selected_sale:
                refund_request.original_amount = selected_sale.amount_paid
            refund_request.save()
            
            # Create refund notification for admin users
            admins = User.objects.filter(Q(role='admin') | Q(is_superuser=True)).distinct()
            for admin in admins:
                if admin != request.user:  # Don't notify yourself if you're an admin
                    UserNotification.create_notification(
                        user=admin,
                        notification_type='refunds',
                        message=f'New refund request: {refund_request.customer_name} - ₦{refund_request.amount:,.2f}',
                        related_id=refund_request.id
                    )
            
            # Also notify the user who created the request
            UserNotification.create_notification(
                user=request.user,
                notification_type='refunds',
                message=f'Your refund request #{refund_request.id} has been submitted for approval',
                related_id=refund_request.id
            )
            
            messages.success(request, f'Refund request #{refund_request.id} created successfully! Pending admin approval.')
            return redirect('refund_requests_list')
            
        except Exception as e:
            print(f"DEBUG - Error creating refund request: {str(e)}")
            messages.error(request, f'Error creating refund request: {str(e)}')
            return redirect('create_refund_request')
    
    # GET request - show form
    context = {
        'action': 'Create',
    }
    return render(request, 'refund_request_form.html', context)


@login_required
@csrf_exempt
def get_customer_sales(request):
    """API endpoint to get ALL customer sales (both paid and debt)"""
    if request.method == 'GET':
        customer_name = request.GET.get('customer_name', '').strip()
        customer_phone = request.GET.get('customer_phone', '').strip()
        
        if not customer_name and not customer_phone:
            return JsonResponse({'success': False, 'error': 'Customer name or phone required'})
        
        # Find ALL customer sales including both paid and debt transactions
        sales = Sale.objects.filter(
            Q(customer_name__iexact=customer_name) |
            Q(customer_phone__iexact=customer_phone)
        ).select_related('staff').prefetch_related('items').order_by('-created_at')  # Last transaction first
        
        sales_data = []
        for sale in sales:
            items_data = []
            for item in sale.items.all():
                items_data.append({
                    'id': item.id,
                    'name': item.product_name,
                    'quantity': item.quantity,
                    'price': float(item.price),
                    'discount': float(item.discount),
                    'total': float(item.total),
                    'max_refund': min(float(item.total), float(sale.amount_paid)),  # Can't refund more than paid
                })
            
            # Calculate maximum refundable amount for the entire sale
            max_sale_refund = min(float(sale.amount_paid), float(sale.total))
            
            sales_data.append({
                'id': sale.id,
                'invoice_number': sale.invoice_number,
                'date': sale.created_at.strftime('%Y-%m-%d %H:%M'),
                'total': float(sale.total),
                'paid': float(sale.amount_paid),
                'balance': float(sale.balance),
                'payment_status': sale.payment_status,
                'max_refund': max_sale_refund,
                'items': items_data,
                'has_balance': sale.balance > 0,
                'is_fully_paid': sale.payment_status == 'paid',
            })
        
        return JsonResponse({
            'success': True,
            'sales': sales_data,
            'count': len(sales_data),
            'message': f'Found {len(sales_data)} transaction(s) for this customer'
        })
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})

@login_required
@csrf_exempt
def edit_refund_request(request, pk):
    """Edit refund request"""
    refund = get_object_or_404(RefundRequest, id=pk)
    
    # Check if user can edit
    if not refund.can_edit() or (refund.created_by != request.user and request.user.role != 'admin'):
        return JsonResponse({'success': False, 'error': 'You cannot edit this refund request'})
    
    if request.method == 'POST':
        try:
            refund.customer_name = request.POST.get('customer_name')
            refund.customer_phone = request.POST.get('customer_phone')
            refund.reason = request.POST.get('reason')
            
            # Update amount with validation
            new_amount = Decimal(request.POST.get('amount'))
            
            # Validate against original amount
            if refund.sale_item and new_amount > refund.sale_item.total:
                return JsonResponse({'success': False, 'error': f'Amount cannot exceed item total (₦{refund.sale_item.total:,.2f})'})
            elif refund.sale and new_amount > refund.sale.total:
                return JsonResponse({'success': False, 'error': f'Amount cannot exceed sale total (₦{refund.sale.total:,.2f})'})
            
            refund.amount = new_amount
            refund.save()
            
            return JsonResponse({'success': True})
            
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})

@login_required
@csrf_exempt
def approve_refund_request(request, pk):
    """Approve and process refund request - FIXED VERSION"""
    if request.method == 'POST':
        try:
            if not (request.user.role == 'admin' or request.user.is_superuser):
                messages.error(request, 'Only admins can approve refunds')
                return redirect('refund_requests_list')
            
            refund_request = RefundRequest.objects.get(id=pk)
            
            if refund_request.status != 'pending':
                messages.error(request, 'This refund request has already been processed')
                return redirect('refund_requests_list')
            
            if refund_request.refund_processed:
                messages.error(request, 'This refund has already been processed')
                return redirect('refund_requests_list')
            
            # Import Decimal here
            from decimal import Decimal, ROUND_HALF_UP
            
            # Get sale - try refund_request.sale first, then find by customer
            sale = refund_request.sale
            if not sale:
                # Find sale by customer info
                sales = Sale.objects.filter(
                    Q(customer_name__iexact=refund_request.customer_name) |
                    Q(customer_phone__iexact=refund_request.customer_phone)
                ).order_by('-created_at')
                
                if sales.exists():
                    sale = sales.first()
            
            if not sale:
                messages.error(request, 'No sale found for this refund request')
                return redirect('refund_requests_list')
            
            # Convert amount to Decimal with proper precision
            refund_amount = Decimal(str(refund_request.amount)).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
            
            # Check if refund amount is valid
            if refund_amount <= Decimal('0'):
                messages.error(request, 'Refund amount must be greater than 0')
                return redirect('refund_requests_list')
            
            # IMPORTANT: Check against what was actually paid (not affected by previous refunds)
            # We need to check the original amount paid before any refunds
            original_payments_total = sale.payments.filter(
                ~Q(payment_method='refund')
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            
            # Get total refunds already processed
            existing_refunds_total = abs(sale.payments.filter(
                payment_method='refund'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00'))
            
            # Calculate maximum refundable amount
            max_refundable = original_payments_total - existing_refunds_total
            
            if refund_amount > max_refundable:
                messages.error(request, 
                    f'Refund amount (₦{refund_amount:,.2f}) exceeds available amount (₦{max_refundable:,.2f})'
                )
                return redirect('refund_requests_list')
            
            # Create refund record
            refund = Refund.objects.create(
                sale=sale,
                refund_request=refund_request,
                amount=refund_amount,
                reason=refund_request.reason,
                payment_method='refund',
                processed_by=request.user
            )
            
            # Update refund request
            refund_request.sale = sale
            refund_request.status = 'approved'
            refund_request.approved_by = request.user
            refund_request.approved_date = timezone.now()
            refund_request.refund_processed = True
            refund_request.save()
            
            # CRITICAL PART: Create payment record for the refund
            Payment.objects.create(
                sale=sale,
                amount=-refund_amount,  # Negative amount for refund
                payment_method='refund',
                reference=f"REFUND-{refund_request.id}",
                notes=f"Refund processed: {refund_request.reason}",
                created_by=request.user
            )
            
            # The Sale model's save() method will automatically recalculate
            # amount_paid and balance when we access it next time
            
            # If refund is for a specific item, adjust inventory
            if refund_request.sale_item and refund_request.sale_item.product:
                item = refund_request.sale_item
                product = item.product
                
                # Calculate proportion of quantity to refund
                if item.total > Decimal('0'):
                    refund_proportion = refund_amount / item.total
                    quantity_to_return = int(round(float(item.quantity) * float(refund_proportion)))
                    
                    if quantity_to_return > 0:
                        product.quantity += quantity_to_return
                        product.save()
                        
                        # Record stock movement
                        StockMovement.objects.create(
                            product=product,
                            movement_type='in',
                            quantity=quantity_to_return,
                            reference=f"REFUND-{refund_request.id}",
                            notes=f"Partial refund for {sale.invoice_number}",
                            created_by=request.user
                        )
            
            messages.success(request, f'Refund of ₦{refund_amount:,.2f} processed successfully!')
            
            # Clear the refund notifications
            if request.user.is_authenticated:
                UserNotification.mark_as_read(request.user, 'refunds')
            
            return redirect('refund_requests_list')
            
        except RefundRequest.DoesNotExist:
            messages.error(request, 'Refund request not found')
        except Exception as e:
            import traceback
            traceback.print_exc()
            messages.error(request, f'Error processing refund: {str(e)}')
    
    return redirect('refund_requests_list')

@login_required
@csrf_exempt
def decline_refund_request(request, pk):
    """Decline refund request"""
    if request.method == 'POST':
        try:
            if not (request.user.role == 'admin' or request.user.is_superuser):
                messages.error(request, 'Only admins can decline refunds')
                return redirect('refund_requests_list')
            
            refund_request = RefundRequest.objects.get(id=pk)
            
            if refund_request.status != 'pending':
                messages.error(request, 'This refund request has already been processed')
                return redirect('refund_requests_list')
            
            refund_request.status = 'declined'
            refund_request.approved_by = request.user
            refund_request.approved_date = timezone.now()
            refund_request.save()
            
            messages.success(request, 'Refund request declined')
            
        except RefundRequest.DoesNotExist:
            messages.error(request, 'Refund request not found')
        except Exception as e:
            messages.error(request, f'Error declining refund: {str(e)}')
    
    return redirect('refund_requests_list')

@login_required
def get_refund_stats(request):
    """Get refund statistics for dashboard"""
    today = timezone.now().date()
    
    # Today's refunds
    today_refunds = Refund.objects.filter(processed_date__date=today).aggregate(
        total=Sum('amount')
    )['total'] or Decimal('0.00')
    
    # Pending refund requests
    pending_requests = RefundRequest.objects.filter(status='pending').count()
    
    # Total refunds this month
    month_start = today.replace(day=1)
    month_refunds = Refund.objects.filter(
        processed_date__date__gte=month_start
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    return JsonResponse({
        'today_refunds': float(today_refunds),
        'pending_requests': pending_requests,
        'month_refunds': float(month_refunds),
    })

# =================== REAL-TIME SEARCH API VIEWS ===================
@login_required
def search_sales_api(request):
    """API endpoint for real-time sales search in dashboard"""
    search_term = request.GET.get('q', '')
    date_filter = request.GET.get('date_filter', 'today')
    
    # Calculate date range
    today = timezone.now().date()
    
    if date_filter == 'today':
        start_date = today
        end_date = today + timedelta(days=1)
    elif date_filter == 'week':
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=7)
    elif date_filter == 'month':
        start_date = today.replace(day=1)
        if start_date.month == 12:
            end_date = start_date.replace(year=start_date.year + 1, month=1, day=1)
        else:
            end_date = start_date.replace(month=start_date.month + 1, day=1)
    elif date_filter == 'year':
        start_date = today.replace(month=1, day=1)
        end_date = today.replace(year=today.year + 1, month=1, day=1)
    else:
        custom_start = request.GET.get('custom_start')
        custom_end = request.GET.get('custom_end')
        if custom_start and custom_end:
            try:
                start_date = datetime.strptime(custom_start, '%Y-%m-%d').date()
                end_date = datetime.strptime(custom_end, '%Y-%m-%d').date() + timedelta(days=1)
            except ValueError:
                start_date = today
                end_date = today + timedelta(days=1)
        else:
            start_date = today
            end_date = today + timedelta(days=1)
    
    # Filter sales
    sales = Sale.objects.filter(
        created_at__range=[start_date, end_date]
    ).select_related('staff').order_by('-created_at')
    
    if search_term:
        sales = sales.filter(
            Q(invoice_number__icontains=search_term) |
            Q(customer_name__icontains=search_term) |
            Q(staff__username__icontains=search_term) |
            Q(customer_phone__icontains=search_term)
        )[:50]
    
    # Serialize results
    results = []
    for sale in sales:
        results.append({
            'id': sale.id,
            'invoice_number': sale.invoice_number,
            'customer_name': sale.customer_name or 'Walk-in',
            'staff_name': sale.staff.username,
            'total': float(sale.total),
            'payment_status': sale.payment_status,
            'created_at': sale.created_at.isoformat(),
        })
    
    return JsonResponse({
        'success': True,
        'results': results,
        'count': len(results),
    })

@login_required
def search_stock_api(request):
    """API endpoint for real-time low stock search"""
    search_term = request.GET.get('q', '')
    
    products = Product.objects.filter(quantity__lte=F('reorder_level')).select_related('category').order_by('quantity')
    
    if search_term:
        products = products.filter(
            Q(name__icontains=search_term) |
            Q(sku__icontains=search_term) |
            Q(category__name__icontains=search_term)
        )[:50]
    
    # Serialize results
    results = []
    for product in products:
        results.append({
            'id': product.id,
            'name': product.name,
            'sku': product.sku,
            'quantity': product.quantity,
            'reorder_level': product.reorder_level,
            'category': product.category.name if product.category else 'N/A',
        })
    
    return JsonResponse({
        'success': True,
        'results': results,
        'count': len(results),
    })

@login_required
def search_products_api(request):
    """API endpoint for real-time products search"""
    search_term = request.GET.get('q', '')
    
    products = Product.objects.all().select_related('category', 'supplier').order_by('name')
    
    if search_term:
        products = products.filter(
            Q(name__icontains=search_term) |
            Q(sku__icontains=search_term) |
            Q(category__name__icontains=search_term) |
            Q(supplier__name__icontains=search_term)
        )[:50]
    
    # Serialize results
    results = []
    for product in products:
        results.append({
            'id': product.id,
            'name': product.name,
            'sku': product.sku,
            'category': product.category.name if product.category else 'N/A',
            'price': float(product.price),
            'quantity': product.quantity,
            'is_low_stock': product.quantity <= product.reorder_level,
        })
    
    return JsonResponse({
        'success': True,
        'results': results,
        'count': len(results),
    })

@login_required
def search_staff_api(request):
    """API endpoint for real-time staff search"""
    search_term = request.GET.get('q', '')
    
    staff = User.objects.filter(is_staff=True).order_by('username')
    
    if search_term:
        staff = staff.filter(
            Q(username__icontains=search_term) |
            Q(first_name__icontains=search_term) |
            Q(last_name__icontains=search_term) |
            Q(email__icontains=search_term) |
            Q(phone__icontains=search_term)
        )[:50]
    
    # Serialize results
    results = []
    for user in staff:
        results.append({
            'id': user.id,
            'username': user.username,
            'first_name': user.first_name or '',
            'last_name': user.last_name or '',
            'email': user.email,
            'phone': user.phone or '',
            'role': user.role,
            'is_active': user.is_active,
            'date_joined': user.date_joined.isoformat() if user.date_joined else None,
        })
    
    return JsonResponse({
        'success': True,
        'results': results,
        'count': len(results),
    })

@login_required
def search_debtors_api(request):
    """API endpoint for real-time debtors search"""
    search_term = request.GET.get('q', '')
    
    debtors = Sale.objects.filter(balance__gt=0).select_related('staff').order_by('-created_at')
    
    if search_term:
        debtors = debtors.filter(
            Q(invoice_number__icontains=search_term) |
            Q(customer_name__icontains=search_term) |
            Q(customer_phone__icontains=search_term) |
            Q(staff__username__icontains=search_term)
        )[:50]
    
    # Serialize results
    results = []
    for sale in debtors:
        # Get payments for this sale
        payments = sale.payments.all()
        payment_history = []
        for payment in payments:
            payment_history.append({
                'amount': float(payment.amount),
                'method': payment.payment_method,
                'reference': payment.reference,
                'date': payment.created_at.isoformat(),
            })
        
        results.append({
            'id': sale.id,
            'invoice_number': sale.invoice_number,
            'customer_name': sale.customer_name or 'Walk-in',
            'customer_phone': sale.customer_phone or '',
            'total': float(sale.total),
            'amount_paid': float(sale.amount_paid),
            'balance': float(sale.balance),
            'created_at': sale.created_at.isoformat(),
            'staff_name': sale.staff.username,
            'payments': payment_history,
        })
    
    return JsonResponse({
        'success': True,
        'results': results,
        'count': len(results),
    })

@login_required
def sales_history_api(request):
    """API endpoint for real-time sales history search"""
    search_term = request.GET.get('q', '')
    
    # Get date range from request (if needed)
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    
    # Filter sales
    sales = Sale.objects.all().select_related('staff').order_by('-created_at')
    
    # Apply date filter if provided
    if date_from and date_to:
        try:
            start_date = datetime.strptime(date_from, '%Y-%m-%d').date()
            end_date = datetime.strptime(date_to, '%Y-%m-%d').date() + timedelta(days=1)
            sales = sales.filter(created_at__range=[start_date, end_date])
        except ValueError:
            pass
    
    # Apply search filter
    if search_term:
        sales = sales.filter(
            Q(invoice_number__icontains=search_term) |
            Q(customer_name__icontains=search_term) |
            Q(customer_phone__icontains=search_term) |
            Q(staff__username__icontains=search_term)
        )[:100]  # Limit to 100 for API response
    
    # Serialize results
    results = []
    for sale in sales:
        results.append({
            'id': sale.id,
            'invoice_number': sale.invoice_number,
            'customer_name': sale.customer_name or 'Walk-in',
            'customer_phone': sale.customer_phone or '',
            'staff_name': sale.staff.username,
            'staff_full_name': f"{sale.staff.first_name or ''} {sale.staff.last_name or ''}".strip(),
            'subtotal': float(sale.subtotal),
            'discount': float(sale.discount),
            'total': float(sale.total),
            'amount_paid': float(sale.amount_paid),
            'balance': float(sale.balance),
            'payment_status': sale.payment_status,
            'created_at': sale.created_at.isoformat(),
            'formatted_date': sale.created_at.strftime('%b %d, %Y %I:%M %p'),
        })
    
    return JsonResponse({
        'success': True,
        'results': results,
        'count': len(results),
    })
    
@login_required
def notification_counts_api(request):
    """API endpoint to get notification counts"""
    return JsonResponse({
        'success': True,
        'dashboard_count': UserNotification.get_unread_count(request.user, 'dashboard'),
        'debtors_count': UserNotification.get_unread_count(request.user, 'debtors'),
        'refunds_count': UserNotification.get_unread_count(request.user, 'refunds'),
        'sales_count': UserNotification.get_unread_count(request.user, 'sales'),
        'total_count': UserNotification.get_unread_count(request.user),
    })

@login_required
@csrf_exempt
def mark_notifications_read(request):
    """Mark notifications as read when user visits a page"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            notification_type = data.get('notification_type')
            
            if notification_type in ['dashboard', 'debtors', 'refunds', 'sales']:
                UserNotification.mark_as_read(request.user, notification_type)
                return JsonResponse({'success': True})
            
            return JsonResponse({'success': False, 'error': 'Invalid notification type'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})

# =================== NEW API VIEWS FOR RECENT SALES ===================

@login_required
def recent_sales_stats_api(request):
    """Get statistics for recent sales"""
    today = timezone.now().date()
    week_ago = today - timedelta(days=7)
    
    # Today's sales count
    today_sales = Sale.objects.filter(created_at__date=today).count()
    
    # Last 7 days sales count
    week_sales = Sale.objects.filter(created_at__date__gte=week_ago).count()
    
    # Current staff's sales today
    staff_sales = Sale.objects.filter(
        staff=request.user,
        created_at__date=today
    ).count()
    
    return JsonResponse({
        'success': True,
        'today_count': today_sales,
        'week_count': week_sales,
        'staff_count': staff_sales,
    })

@login_required
def recent_sales_api(request):
    """Get recent sales (last 24 hours)"""
    yesterday = timezone.now() - timedelta(days=1)
    
    sales = Sale.objects.filter(
        created_at__gte=yesterday
    ).select_related('staff').prefetch_related('items').order_by('-created_at')[:10]
    
    sales_data = []
    for sale in sales:
        items_data = []
        for item in sale.items.all():
            items_data.append({
                'id': item.id,
                'name': item.product_name,
                'quantity': item.quantity,
                'price': float(item.price),
                'discount': float(item.discount),
                'total': float(item.total),
            })
        
        sales_data.append({
            'id': sale.id,
            'invoice_number': sale.invoice_number,
            'customer_name': sale.customer_name,
            'customer_phone': sale.customer_phone,
            'staff_name': sale.staff.username,
            'total': float(sale.total),
            'amount_paid': float(sale.amount_paid),
            'balance': float(sale.balance),
            'payment_status': sale.payment_status,
            'created_at': sale.created_at.isoformat(),
            'formatted_date': sale.created_at.strftime('%b %d, %I:%M %p'),
            'items': items_data,
        })
    
    return JsonResponse({
        'success': True,
        'sales': sales_data,
        'count': len(sales_data),
    })

@login_required
def search_recent_sales_api(request):
    """Search recent sales with flexible search terms"""
    search_term = request.GET.get('q', '').strip()
    yesterday = timezone.now() - timedelta(hours=24)
    
    # Start with recent sales
    sales = Sale.objects.filter(
        created_at__gte=yesterday
    ).select_related('staff').order_by('-created_at')
    
    if search_term:
        # Clean the search term
        search_term = search_term.lower()
        
        # Check if it's an invoice number (case-insensitive)
        if 'inv-' in search_term.lower():
            sales = sales.filter(invoice_number__icontains=search_term.upper())
        
        # Check if it's a date keyword
        elif search_term in ['today', 'now']:
            today = timezone.now().date()
            sales = sales.filter(created_at__date=today)
        
        elif search_term == 'yesterday':
            yesterday_date = timezone.now().date() - timedelta(days=1)
            sales = sales.filter(created_at__date=yesterday_date)
        
        # Check if it's an amount (₦1000 or 1000)
        elif '₦' in search_term or any(char.isdigit() for char in search_term):
            try:
                # Extract numbers from string
                import re
                numbers = re.findall(r'\d+\.?\d*', search_term)
                if numbers:
                    amount = float(numbers[0])
                    # Search in total and amount_paid
                    sales = sales.filter(
                        Q(total=amount) | 
                        Q(amount_paid=amount) |
                        Q(total__gte=amount-0.01) & Q(total__lte=amount+0.01)
                    )
            except (ValueError, TypeError):
                pass
        
        else:
            # General search - check multiple fields
            sales = sales.filter(
                Q(invoice_number__icontains=search_term) |
                Q(customer_name__icontains=search_term) |
                Q(staff__username__icontains=search_term) |
                Q(customer_phone__icontains=search_term)
            )
    
    # Limit results and prefetch items
    sales = sales[:20]
    
    # Prefetch items for each sale
    sales_with_items = []
    for sale in sales:
        items = sale.items.all()
        items_data = []
        for item in items:
            items_data.append({
                'id': item.id,
                'name': item.product_name,
                'quantity': item.quantity,
                'price': float(item.price),
                'discount': float(item.discount),
                'total': float(item.total),
            })
        
        sales_with_items.append({
            'id': sale.id,
            'invoice_number': sale.invoice_number,
            'customer_name': sale.customer_name,
            'customer_phone': sale.customer_phone,
            'staff_name': sale.staff.username,
            'staff_full_name': f"{sale.staff.first_name or ''} {sale.staff.last_name or ''}".strip(),
            'total': float(sale.total),
            'amount_paid': float(sale.amount_paid),
            'balance': float(sale.balance),
            'payment_status': sale.payment_status,
            'created_at': sale.created_at.isoformat(),
            'formatted_date': sale.created_at.strftime('%b %d, %I:%M %p'),
            'items': items_data,
        })
    
    return JsonResponse({
        'success': True,
        'sales': sales_with_items,
        'count': len(sales_with_items),
        'search_term': search_term,
    })

@login_required
def all_sales_api(request):
    """Get all sales with pagination"""
    page = int(request.GET.get('page', 1))
    per_page = 20
    offset = (page - 1) * per_page
    
    sales = Sale.objects.all().select_related('staff').order_by('-created_at')
    
    total_count = sales.count()
    sales = sales[offset:offset + per_page]
    
    sales_data = []
    for sale in sales:
        sales_data.append({
            'id': sale.id,
            'invoice_number': sale.invoice_number,
            'customer_name': sale.customer_name,
            'staff_name': sale.staff.username,
            'total': float(sale.total),
            'amount_paid': float(sale.amount_paid),
            'balance': float(sale.balance),
            'payment_status': sale.payment_status,
            'created_at': sale.created_at.isoformat(),
            'formatted_date': sale.created_at.strftime('%b %d, %Y %I:%M %p'),
        })
    
    return JsonResponse({
        'success': True,
        'sales': sales_data,
        'count': len(sales_data),
        'total_count': total_count,
        'has_more': offset + len(sales_data) < total_count,
        'page': page,
    })

@login_required
def search_all_sales_api(request):
    """Search all sales"""
    search_term = request.GET.get('q', '').lower().strip()
    page = int(request.GET.get('page', 1))
    per_page = 20
    offset = (page - 1) * per_page
    
    sales = Sale.objects.all().select_related('staff').order_by('-created_at')
    
    if search_term:
        # Try different search strategies
        if search_term.startswith('inv-'):
            sales = sales.filter(invoice_number__icontains=search_term.upper())
        elif search_term in ['today', 'now']:
            today = timezone.now().date()
            sales = sales.filter(created_at__date=today)
        elif search_term == 'yesterday':
            yesterday_date = timezone.now().date() - timedelta(days=1)
            sales = sales.filter(created_at__date=yesterday_date)
        elif search_term.startswith('₦') or search_term.replace('.', '').isdigit():
            try:
                amount = float(search_term.replace('₦', ''))
                sales = sales.filter(Q(total=amount) | Q(amount_paid=amount))
            except ValueError:
                pass
        else:
            sales = sales.filter(
                Q(invoice_number__icontains=search_term) |
                Q(customer_name__icontains=search_term) |
                Q(staff__username__icontains=search_term) |
                Q(customer_phone__icontains=search_term)
            )
    
    total_count = sales.count()
    sales = sales[offset:offset + per_page]
    
    sales_data = []
    for sale in sales:
        sales_data.append({
            'id': sale.id,
            'invoice_number': sale.invoice_number,
            'customer_name': sale.customer_name,
            'staff_name': sale.staff.username,
            'total': float(sale.total),
            'amount_paid': float(sale.amount_paid),
            'balance': float(sale.balance),
            'payment_status': sale.payment_status,
            'created_at': sale.created_at.isoformat(),
            'formatted_date': sale.created_at.strftime('%b %d, %Y %I:%M %p'),
        })
    
    return JsonResponse({
        'success': True,
        'sales': sales_data,
        'count': len(sales_data),
        'total_count': total_count,
        'has_more': offset + len(sales_data) < total_count,
        'page': page,
    })

@login_required
def sale_details_api(request, pk):
    """Get detailed information about a specific sale"""
    try:
        sale = Sale.objects.select_related('staff').prefetch_related('items').get(id=pk)
        
        items_data = []
        for item in sale.items.all():
            items_data.append({
                'id': item.id,
                'name': item.product_name,
                'quantity': item.quantity,
                'price': float(item.price),
                'discount': float(item.discount),
                'total': float(item.total),
            })
        
        sale_data = {
            'id': sale.id,
            'invoice_number': sale.invoice_number,
            'customer_name': sale.customer_name,
            'customer_phone': sale.customer_phone,
            'staff_name': sale.staff.username,
            'staff_full_name': f"{sale.staff.first_name or ''} {sale.staff.last_name or ''}".strip(),
            'total': float(sale.total),
            'amount_paid': float(sale.amount_paid),
            'balance': float(sale.balance),
            'payment_status': sale.payment_status,
            'created_at': sale.created_at.isoformat(),
            'formatted_date': sale.created_at.strftime('%b %d, %Y %I:%M %p'),
            'items': items_data,
        }
        
        return JsonResponse({
            'success': True,
            'sale': sale_data,
        })
        
    except Sale.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Sale not found',
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e),
        })