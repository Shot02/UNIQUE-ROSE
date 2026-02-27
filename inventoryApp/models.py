from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator
from decimal import Decimal, ROUND_HALF_UP
from django.utils import timezone
import uuid
from django.contrib.auth.models import User

class User(AbstractUser):
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('staff', 'Staff'),
        ('manager', 'Manager'),
    ]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='staff')
    phone = models.CharField(max_length=15, blank=True)
    address = models.TextField(blank=True)
    
    class Meta:
        db_table = 'users'
    
    def __str__(self):
        return f"{self.username} ({self.role})"

class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'categories'
        verbose_name_plural = 'Categories'
    
    def __str__(self):
        return self.name


class Customer(models.Model):
    """Customer information for loyalty and tracking"""
    CUSTOMER_TYPE = [
        ('regular', 'Regular'),
        ('vip', 'VIP'),
        ('wholesale', 'Wholesale'),
    ]
    
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=15, unique=True)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(blank=True)
    customer_type = models.CharField(max_length=20, choices=CUSTOMER_TYPE, default='regular')
    loyalty_points = models.IntegerField(default=0)
    total_purchases = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    last_purchase_date = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'customers'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['phone']),
            models.Index(fields=['customer_type']),
        ]
    
    def __str__(self):
        return f"{self.name} ({self.phone})"
    
    def update_purchase_stats(self, amount):
        """Update customer purchase statistics"""
        self.total_purchases += amount
        self.last_purchase_date = timezone.now()
        self.loyalty_points += int(amount / 10)  # 1 point per ₦10 spent
        self.save()


class Supplier(models.Model):
    name = models.CharField(max_length=200)
    contact_person = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=15)
    address = models.TextField(blank=True)
    website = models.URLField(blank=True, null=True)
    tax_id = models.CharField(max_length=50, blank=True, null=True)
    payment_terms = models.CharField(max_length=100, blank=True, default='Cash on Delivery')
    lead_time_days = models.IntegerField(default=7, validators=[MinValueValidator(0)])
    rating = models.IntegerField(default=3, choices=[(1, '1 Star'), (2, '2 Stars'), (3, '3 Stars'), (4, '4 Stars'), (5, '5 Stars')])
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'suppliers'
        ordering = ['name']
    
    def __str__(self):
        return self.name
    
    @property
    def total_products(self):
        return self.product_set.count()
    
    @property
    def total_purchases(self):
        from decimal import Decimal
        total = self.product_set.aggregate(total=models.Sum('quantity'))['total'] or 0
        return total


class Product(models.Model):
    name = models.CharField(max_length=200, default='Unnamed Product')
    sku = models.CharField(max_length=50, unique=True, editable=False, blank=True)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    quantity = models.IntegerField(default=0)
    reorder_level = models.IntegerField(default=10)
    # New expiry fields
    expiry_date = models.DateField(null=True, blank=True, help_text="Product expiry date")
    manufacturing_date = models.DateField(null=True, blank=True, help_text="Date of manufacture")
    batch_number = models.CharField(max_length=50, blank=True, null=True, help_text="Batch/Lot number")
    # Additional fields
    location = models.CharField(max_length=100, blank=True, null=True, help_text="Shelf/Storage location")
    is_active = models.BooleanField(default=True)
    image = models.ImageField(upload_to='products/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'products'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['expiry_date']),
            models.Index(fields=['supplier']),
            models.Index(fields=['name']),
        ]
    
    def __str__(self):
        return f"{self.name} ({self.sku})"
    
    def save(self, *args, **kwargs):
        if not self.sku:
            self.sku = f"PRD-{uuid.uuid4().hex[:6].upper()}"
            while Product.objects.filter(sku=self.sku).exists():
                self.sku = f"PRD-{uuid.uuid4().hex[:6].upper()}"
        
        if not self.name or self.name.strip() == '':
            self.name = 'Unnamed Product'
        
        if self.cost_price > self.price:
            self.cost_price = self.price
            
        super().save(*args, **kwargs)
    
    @property
    def is_low_stock(self):
        return self.quantity <= self.reorder_level
    
    @property
    def stock_status(self):
        if self.quantity == 0:
            return 'out_of_stock'
        elif self.quantity <= self.reorder_level:
            return 'low_stock'
        else:
            return 'in_stock'
    
    @property
    def expiry_status(self):
        """Get expiry status of product"""
        from django.utils import timezone
        from datetime import timedelta
        
        if not self.expiry_date:
            return 'no_expiry'
        
        today = timezone.now().date()
        days_until_expiry = (self.expiry_date - today).days
        
        if days_until_expiry < 0:
            return 'expired'
        elif days_until_expiry <= 30:
            return 'expiring_soon'
        elif days_until_expiry <= 90:
            return 'expiring'
        else:
            return 'valid'
    
    @property
    def expiry_badge(self):
        """Get HTML badge for expiry status"""
        status = self.expiry_status
        if status == 'expired':
            return '<span class="badge badge-danger">Expired</span>'
        elif status == 'expiring_soon':
            return '<span class="badge badge-warning">Expiring Soon</span>'
        elif status == 'expiring':
            return '<span class="badge badge-info">Expiring</span>'
        elif status == 'valid':
            return '<span class="badge badge-success">Valid</span>'
        else:
            return '<span class="badge badge-secondary">No Expiry</span>'
    
    @property
    def days_until_expiry(self):
        """Get days until expiry"""
        from django.utils import timezone
        if not self.expiry_date:
            return None
        today = timezone.now().date()
        return (self.expiry_date - today).days
    
    
    
class Sale(models.Model):
    invoice_number = models.CharField(max_length=50, unique=True)
    staff = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    customer_name = models.CharField(max_length=200, blank=True)
    customer_phone = models.CharField(max_length=15, blank=True)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    payment_status = models.CharField(max_length=20, choices=[
        ('paid', 'Paid'),
        ('partial', 'Partial Payment'),
        ('unpaid', 'Unpaid')
    ], default='paid')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'sales'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Invoice {self.invoice_number}"
    
    def save(self, *args, **kwargs):
        # Calculate balance
        self.balance = self.total - self.amount_paid
        
        # Update payment status based on balance
        if self.balance <= 0:
            self.payment_status = 'paid'
        elif self.balance < self.total:
            self.payment_status = 'partial'
        else:
            self.payment_status = 'unpaid'
            
        super().save(*args, **kwargs)
    
    @property
    def is_debtor(self):
        return self.balance > 0
    
    @property
    def items_count(self):
        return self.items.count() if hasattr(self, 'items') else 0
    
    def save(self, *args, **kwargs):
        decimal_fields = ['subtotal', 'discount', 'total', 'amount_paid', 'balance']
        
        for field in decimal_fields:
            value = getattr(self, field)
            if not isinstance(value, Decimal):
                try:
                    # Convert to string first to avoid float precision issues
                    setattr(self, field, Decimal(str(value)).quantize(
                        Decimal('0.01'), rounding=ROUND_HALF_UP
                    ))
                except:
                    setattr(self, field, Decimal('0.00'))
        
        # Calculate balance (this will be a Decimal)
        self.balance = Decimal(str(self.total)) - Decimal(str(self.amount_paid))
        
        # Ensure balance is never negative
        if self.balance < Decimal('0'):
            self.balance = Decimal('0')
        
        # Update payment status
        if self.balance <= Decimal('0'):
            self.payment_status = 'paid'
        elif self.balance < self.total:
            self.payment_status = 'partial'
        else:
            self.payment_status = 'unpaid'
            
        super().save(*args, **kwargs)
    
    @property
    def net_amount_paid(self):
        """Calculate actual amount paid excluding refunds"""
        from decimal import Decimal
        from django.db.models import Q, Sum
        
        # Get all payments that are not refunds
        non_refund_payments = self.payments.filter(
            ~Q(payment_method='refund')
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        # Get refunds (these are negative amounts)
        refunds = self.payments.filter(
            payment_method='refund'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        # Calculate net amount (positive payments minus refunds)
        net_paid = non_refund_payments + refunds  # refunds are negative, so this subtracts them
        
        # Ensure it's not negative
        if net_paid < Decimal('0'):
            net_paid = Decimal('0')
            
        return net_paid
    
    @property
    def is_real_debtor(self):
        """Check if customer actually owes money after accounting for refunds"""
        return self.net_amount_paid < self.total
    
    
class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    product_name = models.CharField(max_length=200)
    quantity = models.IntegerField(validators=[MinValueValidator(1)])
    price = models.DecimalField(max_digits=10, decimal_places=2)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    
    class Meta:
        db_table = 'sale_items'
    
    def save(self, *args, **kwargs):
        self.total = (self.price * self.quantity) - self.discount
        super().save(*args, **kwargs)

class Payment(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=50, choices=[
        ('cash', 'Cash'),
        ('card', 'Card'),
        ('transfer', 'Bank Transfer'),
    ], default='cash')
    reference = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    
    class Meta:
        db_table = 'payments'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Payment of ₦{self.amount} for {self.sale.invoice_number}"

class StockMovement(models.Model):
    MOVEMENT_TYPES = [
        ('in', 'Stock In'),
        ('out', 'Stock Out'),
        ('adjustment', 'Adjustment'),
    ]
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_TYPES)
    quantity = models.IntegerField()
    reference = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'stock_movements'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.movement_type} - {self.product.name} ({self.quantity})"

class PendingCart(models.Model):
    staff = models.ForeignKey(User, on_delete=models.CASCADE)
    cart_data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'pending_carts'
        ordering = ['-created_at']
        unique_together = ['staff']  # One pending cart per staff
    
    def __str__(self):
        return f"Pending cart for {self.staff.username}"

class SavedCart(models.Model):
    staff = models.ForeignKey(User, on_delete=models.CASCADE)
    cart_name = models.CharField(max_length=100, default="Unsaved Cart")
    cart_data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'saved_carts'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.cart_name} - {self.staff.username}"
    
    @property
    def items_count(self):
        if self.cart_data and 'items' in self.cart_data:
            return len(self.cart_data['items'])
        return 0
    
    @property
    def total_amount(self):
        if self.cart_data and 'items' in self.cart_data:
            total = sum(
                (item.get('price', 0) * item.get('quantity', 1)) - item.get('discount', 0)
                for item in self.cart_data['items']
            )
            return Decimal(str(total))
        return Decimal('0.00')

class RefundRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('declined', 'Declined'),
    ]
    
    sale = models.ForeignKey('Sale', on_delete=models.CASCADE, null=True, blank=True, related_name='refund_requests')
    sale_item = models.ForeignKey('SaleItem', on_delete=models.SET_NULL, null=True, blank=True)
    customer_name = models.CharField(max_length=200)
    customer_phone = models.CharField(max_length=15)
    reason = models.TextField()
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    original_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)  # Original sale item amount
    request_date = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_refunds')
    approved_date = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_refunds')
    refund_processed = models.BooleanField(default=False)
    
    class Meta:
        db_table = 'refund_requests'
        ordering = ['-request_date']
    
    def __str__(self):
        return f"Refund #{self.id} - {self.customer_name} - ₦{self.amount}"
    
    def can_edit(self):
        return self.status == 'pending'
    
    def can_approve_decline(self, user):
        return self.status == 'pending' and (user.role == 'admin' or user.is_superuser)
    
    def get_related_sales(self):
        """Get all sales for this customer"""
        return Sale.objects.filter(
            Q(customer_name__iexact=self.customer_name) |
            Q(customer_phone__iexact=self.customer_phone)
        ).order_by('-created_at')
        

class Refund(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.SET_NULL, null=True, blank=True, related_name='refunds')
    refund_request = models.OneToOneField(RefundRequest, on_delete=models.CASCADE, null=True, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    reason = models.TextField()
    payment_method = models.CharField(max_length=50, choices=[
        ('cash', 'Cash'),
        ('card', 'Card'),
        ('transfer', 'Bank Transfer'),
        ('refund', 'Refund Adjustment'),
    ], default='cash')
    processed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    processed_date = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'refunds'
        ordering = ['-processed_date']
    
    def __str__(self):
        sale_ref = self.sale.invoice_number if self.sale else "No Sale"
        return f"Refund #{self.id} - {sale_ref} - ₦{self.amount}"
    
    def get_customer_name(self):
        """Get customer name from sale or refund request"""
        if self.sale and self.sale.customer_name:
            return self.sale.customer_name
        elif self.refund_request:
            return self.refund_request.customer_name
        return "Unknown Customer"
    
    def save(self, *args, **kwargs):
        # Ensure amount is properly rounded
        if not isinstance(self.amount, Decimal):
            self.amount = Decimal(str(self.amount)).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
        
        # Don't allow negative refunds
        if self.amount < Decimal('0'):
            self.amount = Decimal('0')
            
        super().save(*args, **kwargs)
    
    def get_linked_sale(self):
        """Get the sale linked to this refund"""
        if self.sale:
            return self.sale
        elif self.refund_request and self.refund_request.sale:
            return self.refund_request.sale
        return None
    
class UserNotification(models.Model):
    NOTIFICATION_TYPES = [
        ('dashboard', 'Dashboard Update'),
        ('debtors', 'New Debtors'),
        ('refunds', 'New Refund Requests'),
        ('sales', 'New Sales'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES)
    message = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    related_id = models.IntegerField(null=True, blank=True)  # ID of related object (sale, refund, etc.)
    
    class Meta:
        db_table = 'user_notifications'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_read']),
            models.Index(fields=['user', 'notification_type', 'is_read']),
        ]
    
    def __str__(self):
        return f"{self.notification_type} notification for {self.user.username}"
    
    @classmethod
    def mark_as_read(cls, user, notification_type):
        """Mark all notifications of a type as read for a user"""
        cls.objects.filter(
            user=user,
            notification_type=notification_type,
            is_read=False
        ).update(is_read=True, created_at=timezone.now())
    
    @classmethod
    def create_notification(cls, user, notification_type, message='', related_id=None):
        """Create a new notification for user"""
        return cls.objects.create(
            user=user,
            notification_type=notification_type,
            message=message,
            related_id=related_id,
            is_read=False
        )
    
    @classmethod
    def get_unread_count(cls, user, notification_type=None):
        """Get count of unread notifications for user"""
        query = cls.objects.filter(user=user, is_read=False)
        if notification_type:
            query = query.filter(notification_type=notification_type)
        return query.count()