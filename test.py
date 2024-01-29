from flask import Flask, render_template, request
from bs4 import BeautifulSoup
import requests
import nltk
from nltk.tokenize import word_tokenize
from nltk.corpus import stopwords
from googlesearch import search
import json
from urllib.parse import urlparse
import re
import time  # Import the 'time' module
from requests.exceptions import HTTPError
import logging

app = Flask(__name__)

def truncate_amazon_url(url):
    match = re.search(r'/dp/(\w+)', url)

    if match:
        asin = match.group(1)
        truncated_url = f'https://www.amazon.in/dp/{asin}'
        return truncated_url
    else:
        return url

# Function to extract keywords from text
def extract_keywords(text, num_keywords=None):
    words = word_tokenize(text)
    stop_words = set(stopwords.words('english'))
    filtered_words = [word.lower() for word in words if word.isalnum() and word.lower() not in stop_words]
    if num_keywords is not None:
        return filtered_words[:num_keywords]
    else:
        return filtered_words


# Function to generate Google Shopping URL for a query
def get_google_shopping_url(query):
    base_url = "https://www.google.co.in/search?"
    params = {"q": query, "tbm": "shop"}
    google_url = base_url + "&".join([f"{key}={value}" for key, value in params.items()])
    return google_url


logging.basicConfig(level=logging.INFO)  # Set logging level to INFO

def search_product_with_retry(keywords, num_results=5, max_retries=3):
    for attempt in range(max_retries):
        try:
            logging.info(f"Attempting search attempt {attempt + 1}")
            # Fetch a fixed number of search results without specifying num_results
            search_results_iterator = search(keywords, num_results=num_results)
            search_results = []

            # Collect the results
            for result in search_results_iterator:
                source_name = urlparse(result).netloc
                search_results.append({"source_name": source_name, "link": result})

            return search_results
        except HTTPError as e:
            if e.response.status_code == 429:
                # Handle 429 error (Too Many Requests)
                wait_time = 2 ** attempt  # Exponential backoff
                logging.warning(f"Received 429 error. Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                # For other HTTP errors, raise the exception
                logging.error(f"HTTP Error: {e.response.status_code}")
                raise e
        except Exception as ex:
            logging.error(f"Error occurred: {ex}")
            raise ex

    # If all retries fail, raise an exception
    logging.error("Failed to fetch search results after multiple retries")
    raise Exception("Failed to fetch search results after multiple retries")


# Function to check for drip pricing indicators
def check_drip_pricing(prod_features):
    drip_indicators = ["fee", "charge", "tax", "shipping", "total"]
    return any(indicator in prod_features.lower() for indicator in drip_indicators)

# Function to check for actual drip pricing based on script content
def check_actual_drip_pricing(script_content):
    if isinstance(script_content, list) and script_content:
        for element in script_content:
            if "requires_shipping" in element and "taxable" in element:
                requires_shipping = str(element["requires_shipping"]).lower()
                taxable = str(element["taxable"]).lower()

                if requires_shipping == "true" and taxable == "true":
                    return True

    return False


# Function to extract script content from HTML soup
def extract_script_content(soup):
    script_element = soup.find("script", id="em_product_variants", type="application/json")
    script_content = []
    if script_element:
        try:
            script_content = json.loads(script_element.string)
        except json.JSONDecodeError as e:
            print("Error decoding JSON:", e)
            print("JSON Content:", script_element.string)
    return script_content

def extract_product_info_amazon(url, max_attempts=3):
    base_url = 'https://www.amazon.in'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
        'Accept-Language': 'en-US,en;q=0.5',
        'Content-Type': 'application/json',
        'tz': 'GMT+00:00'
    }

    base_response = requests.get(base_url, headers=headers)
    cookies = base_response.cookies

    attempts = 0
    while attempts < max_attempts:
        product_response = requests.get(url, headers=headers, cookies=cookies)
        product_response.raise_for_status()
        soup = BeautifulSoup(product_response.text, features='lxml')
        prod_name_element = soup.find('span', attrs={"class": "a-size-large product-title-word-break"})

        price_lines = soup.find_all(class_="a-price-whole")
        prod_features_element = soup.find("div", class_="a-section a-spacing-medium a-spacing-top-small")
        prod_features = prod_features_element.get_text(strip=True) if prod_features_element else "N/A"
        img_container = soup.find("div", id="imgTagWrapperId")

        if img_container:
            img_src_element = img_container.find("img")
            img_src = img_src_element.get("src") if img_src_element else "N/A"
        else:
            img_src = "N/A"
        
        shipping = soup.find("div", id="mir-layout-DELIVERY_BLOCK")
        
        # Extract delivery date and condition
        shipping_details = extract_shipping_details(shipping)

        if prod_name_element:
            prod_name = prod_name_element.get_text(strip=True)
            
            # Check if the price is available
            if price_lines:
                prod_price = str(price_lines[0].text.strip())
            else:
                prod_price = "Price not found"
                
            return prod_name, prod_price, prod_features, img_src, shipping_details
        else:
            attempts += 1
            time.sleep(5)

    return "Product not found on Amazon after multiple attempts", "", "", "", ""


def extract_shipping_details(shipping_element):
    if shipping_element:
        delivery_info = {}
        primary_delivery_message = shipping_element.find('div', id='mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_LARGE')
        if primary_delivery_message:
            delivery_info['delivery_date'] = primary_delivery_message.find('span', class_='a-text-bold').text.strip()
            delivery_info['delivery_condition'] = primary_delivery_message.find('span', class_='a-text-bold').next_sibling.strip()
            delivery_info['delivery_type']=primary_delivery_message.find('a',class_='a-link-normal').text.strip()
        return delivery_info
    else:
        return {'delivery_date': 'N/A', 'delivery_condition': 'N/A','delivery_type':'N/A'}



def extract_product_info_ebay(soup):
    prod_name_element = soup.find("div", class_="vim x-item-title").find("span", class_="ux-textspans--BOLD")
    prod_name = prod_name_element.text.strip() if prod_name_element else "N/A"
    prod_url = get_google_shopping_url(prod_name)

    img_src_element = soup.find("div", class_="ux-image-carousel-item image-treatment active image").find("img")
    img_src = img_src_element.get("src") if img_src_element else "N/A"

    shipping_details_container = soup.find("div", class_="ux-labels-values__values-content")
    shipping_details = None
    if shipping_details_container:
        shipping_details_elements = shipping_details_container.find_all("span", class_="ux-textspans")
        for element in shipping_details_elements:
            if "ux-textspans--BOLD" in element.get("class", []):
                shipping_details = element.get_text(strip=True)
                break

    prod_price_element_container = soup.find("div", class_="vim x-bin-price")
    prod_price = "N/A"
    if prod_price_element_container:
        prod_price_element = prod_price_element_container.find("span", class_="ux-textspans")
        prod_price_text = prod_price_element.text.strip() if prod_price_element else "N/A"
        prod_price = extract_numeric_price(prod_price_text)

    # Extracting product features
    prod_features_container = soup.find("div", class_="vim x-product-details")
    if prod_features_container:
        prod_features_elements = prod_features_container.find_all("span", class_="ux-textspans")
        prod_features = [element.get_text(strip=True) for element in prod_features_elements]
        prod_features = ", ".join(prod_features)  # Joining multiple features into a single string
    else:
        prod_features = "N/A"

    return prod_name, prod_url, img_src, shipping_details, prod_price, prod_features


def extract_numeric_price(price_text):
    # Extract numeric part of the price string
    numeric_part = re.search(r'\d+\.\d+', price_text)
    if numeric_part:
        return float(numeric_part.group())
    else:
        return "N/A"



def extract_product_info_deodap(soup):
    prod_name_element = soup.find("div", class_="product-block product-block--title product-block--first").find("h1", class_="product-title")
    prod_name = prod_name_element.text.strip() if prod_name_element else "N/A"
    prod_url = get_google_shopping_url(prod_name)
    

    prod_price_element_container = soup.find("div", class_="price__current price__current--on-sale")

    if prod_price_element_container:
        prod_price_element = prod_price_element_container.find("span", class_="money")
        prod_price = prod_price_element.text.strip()[4:] if prod_price_element else "N/A"
    else:
        prod_price = "N/A"

    prod_features_element = soup.find("div", class_="product-description rte")

    img_src_element = soup.find("div", class_="product-gallery--image-background").find("img")

    if img_src_element:
        img_src = img_src_element.get("src")
    else:
        img_src = "N/A"

    prod_features = prod_features_element.get_text(strip=True) if prod_features_element else "N/A"

    return prod_name, prod_price, prod_features, prod_url, img_src


def extract_product_info_flipkart(soup, max_attempts=3):
    attempts = 0
    while attempts < max_attempts:
        prod_name_element = soup.find('span', class_='B_NuCI')
        price_element = soup.find('div', class_='_30jeq3 _16Jk6d')
        prod_features_element = soup.find('div', class_='_2o-xpa')
        delivery_info = "N/A"  # Default value for delivery information

        # Find the product image element
        img_element = soup.find('img', class_='_396cs4')

        if img_element:
            img_src = img_element['src']
        else:
            img_src = "N/A"

        # Check if the price element is found on the page
        if price_element:
            price = price_element.text
        else:
            price = "Price not found"

        # Check if the product name element is found on the page
        if prod_name_element:
            prod_name = prod_name_element.text.strip()
        else:
            prod_name = "Product name not found"

        # Remove Rs symbol from price
        price_without_Rs = price[1:]
        # Remove commas from price
        price_without_comma = price_without_Rs.replace(",", "")
        # Convert price from string to int
        prod_price = int(price_without_comma)

        prod_features = prod_features_element.get_text(strip=True) if prod_features_element else "N/A"

        # Extract shipping details based on the provided criteria
        delivery_element = soup.find('div', class_='_3XINqE')
        if delivery_element:
            try:
                # Check if the span class "_1rQTjC" is present for free delivery
                free_delivery_span = delivery_element.find('span', class_='_1rQTjC')
                if free_delivery_span:
                    delivery_info = free_delivery_span.text.strip() + " delivery"
                else:
                    # If "_1rQTjC" class is not present, extract the shipping cost from "_2W3miC"
                    shipping_cost_span = delivery_element.find('span', class_='_2W3miC')
                    if shipping_cost_span:
                        delivery_info = "₹" + shipping_cost_span.text.strip()
            except Exception as e:
                print("Error:", e)

        # Increment the attempts and wait for a few seconds before retrying
        attempts += 1
        time.sleep(5)

    return prod_name, prod_price, img_src, prod_features, delivery_info




@app.route('/', methods=['GET', 'POST'])
def index():
    prod_name = "N/A"
    search_results = "N/A"  # Set search_results to "N/A" initially
    prod_url = ""
    prod_features = "N/A"
    img_src = "N/A"
    shipping_details = "N/A"
    prod_price = "N/A" 

    if request.method == 'POST':
        url = request.form['url']
        parsed_url = urlparse(url)

        if 'deodap.in' in parsed_url.netloc:
            source = 'deodap'
        elif 'www.ebay' in parsed_url.netloc:
            source = 'ebay'
        elif 'www.amazon' in parsed_url.netloc:
            source = 'amazon'
        elif 'www.flipkart' in parsed_url.netloc:
            source = 'flipkart'
        else:
            source = 'unknown'

        HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
                   'Accept-Language': 'en-US,en;q=0.5', 'Content-Type': 'application/json', 'tz': 'GMT+00:00'}

        webpage = requests.get(url, headers=HEADERS)
        soup = BeautifulSoup(webpage.content, 'html.parser')

        if source == 'ebay':
            prod_name, prod_url, img_src, shipping_details, prod_price, prod_features = extract_product_info_ebay(soup)
            # Perform Google search using product name
            search_results = search_product_with_retry(prod_name, num_results=5, max_retries=3)
        elif source == 'deodap':
            prod_name, prod_price, prod_features, prod_url, img_src = extract_product_info_deodap(soup)
            # Perform Google search using product name
            if prod_name:
                search_results = search_product_with_retry(prod_name, num_results=5, max_retries=5)
            else:
                # Handle case where prod_name is None or empty
                search_results = []

        elif source == 'amazon':
            truncated_url = truncate_amazon_url(url)
            prod_name, prod_price, prod_features, img_src, shipping_details = extract_product_info_amazon(truncated_url)
            search_results = search_product_with_retry(prod_name, num_results=5, max_retries=5)
        elif source=='flipkart':
            prod_name, prod_price, img_src,prod_features,shipping_details=extract_product_info_flipkart(soup)

            # Extract keywords for searching based on product name and features
            search_keywords = prod_name + ' ' + prod_features
            try:
                search_results = search_product_with_retry(search_keywords, num_results=5, max_retries=3)
            except Exception as e:
                print("Failed to fetch search results:", e)
                search_results = "N/A"

        # Move the price extraction logic here and handle if prod_price is still "N/A"
        if prod_price != "N/A" and prod_price != "":
            # Check if prod_price is already a string
            if isinstance(prod_price, str):
                prod_price_numeric = float(prod_price.replace(',', ''))
            else:
                prod_price_numeric = prod_price  # Assume prod_price is already numeric
            predicted_price = prod_price_numeric + (prod_price_numeric * 0.18) + 99 + 17.82
            predicted_price = round(predicted_price, 2)
        else:
            prod_price_numeric = 0.0  # Initialize prod_price_numeric with a default value
            predicted_price = "N/A"

        prod_features = prod_features if prod_features != "N/A" else "No product features available."

        # Extract keywords from the product name
        nltk.download('punkt')
        nltk.download('stopwords')

        keywords = extract_keywords(prod_name) if prod_name != "N/A" else []

        # Convert keywords list to a string
        keywords_str = ' '.join(keywords)

        drip_pricing_detected = check_drip_pricing(prod_features)

        script_content = extract_script_content(soup)

        actual_drip_pricing_detected = check_actual_drip_pricing(script_content)

        return render_template('result.html', url=url, prod_name=prod_name, prod_price=prod_price,
                               prod_features=prod_features, search_results=search_results,
                               drip_pricing_detected=drip_pricing_detected,
                               actual_drip_pricing_detected=actual_drip_pricing_detected,
                               predicted_price=predicted_price, img_src=img_src, shipping_details=shipping_details,
                               selected_option=source, prod_url=prod_url
                               )

    return render_template('index.html')



if __name__ == '__main__':
    app.run(debug=True)
