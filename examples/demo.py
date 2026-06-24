"""A small program to debug with nobug."""
from MyClass import MyClass


def total_price(items, tax_rate):
    subtotal = 0
    for name, price in items:
        subtotal = subtotal + price
    tax = subtotal * tax_rate
    return subtotal + tax


def main():
    mine = MyClass()
    mine.answer()
    cart = [("apple", 30), ("bread", 25), ("milk", 45)]
    grand_total = total_price(cart, 0.08)
    print("Total:", grand_total)
    x = [str((4 + 7) / (6 + 8))]
    print(x)


main()
