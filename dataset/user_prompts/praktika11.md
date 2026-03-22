Необходимо написать отчет по Практической работе №11

Код создания базы данных:
CREATE DATABASE SmartStoreDb;
GO
USE SmartStoreDb;
GO
CREATE TABLE Company (
    companyId INT PRIMARY KEY,
    companyName VARCHAR(100) NOT NULL
);

CREATE TABLE Category (
    categoryId INT PRIMARY KEY,
    categoryName VARCHAR(100) NOT NULL
);

CREATE TABLE Country (
    countryId INT PRIMARY KEY,
    countryName VARCHAR(100) NOT NULL
);

CREATE TABLE Manager (
    managerId INT PRIMARY KEY,
    lastName VARCHAR(50) NOT NULL,
    firstName VARCHAR(50) NOT NULL,
    fatherName VARCHAR(50),
    position VARCHAR(50) NOT NULL
);

CREATE TABLE Product (
 productId INT PRIMARY KEY,
 companyId INT FOREIGN KEY REFERENCES Company(companyId),
 countryId INT FOREIGN KEY REFERENCES Country(countryId),
 categoryId INT FOREIGN KEY REFERENCES Category(categoryId),
 model VARCHAR(50) NOT NULL,
 color VARCHAR(50),
 numSIM INT,
 character VARCHAR(150),
 price DECIMAL(10,2) NOT NULL,
 quantity INT NOT NULL
);

CREATE TABLE Purchase (
 purchaseId INT PRIMARY KEY,
 productId INT FOREIGN KEY REFERENCES Product(productId),
 managerId INT FOREIGN KEY REFERENCES Manager(managerId),
 count INT NOT NULL,
 purchasePrice DECIMAL(10,2) NOT NULL,
 datePurchase DATETIME NOT NULL
);


Код приложения на C# (WinForms) - бери куски кода отсюда, если их нужно показать в каких либо пунктах:
using System;
using System.Data;
using System.Data.SqlClient;
using System.Windows.Forms;
using Microsoft.Data.SqlClient;

namespace CommonProgramming
{
    public partial class SmartStore : Form
    {
        // Строка подключения к вашей базе данных.
        // Замените значение на свое, скопировав его из SQL Server Management Studio.
        private string ConnectionString = "Server=localhost;Database=SmartStoreDB;Trusted_Connection=True;TrustServerCertificate=True;";

        public SmartStore()
        {
            InitializeComponent();
            LoadReferenceDataToComboBoxes();
        }

        /// <summary>
        /// Метод для загрузки данных из БД в DataGridView.
        /// </summary>
        private void BindDataToGrid(string sqlQuery, DataGridView dataGridView)
        {
            try
            {
                using (SqlConnection connection = new SqlConnection(ConnectionString))
                using (SqlDataAdapter adapter = new SqlDataAdapter(sqlQuery, connection))
                {
                    DataTable dataTable = new DataTable();
                    adapter.Fill(dataTable);
                    dataGridView.DataSource = dataTable;
                    
                    // Автоподстройка ширины колонок под содержимое
                    foreach (DataGridViewColumn column in dataGridView.Columns)
                    {
                        column.AutoSizeMode = DataGridViewAutoSizeColumnMode.AllCells;
                    }
                }
            }
            catch (Exception ex)
            {
                MessageBox.Show($"Ошибка при загрузке данных: {ex.Message}", "Ошибка", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        /// <summary>
        /// Метод для заполнения ComboBox данными из БД.
        /// </summary>
        private void FillComboBox(string sqlQuery, ComboBox comboBox)
        {
            try
            {
                using (SqlConnection connection = new SqlConnection(ConnectionString))
                using (SqlDataAdapter adapter = new SqlDataAdapter(sqlQuery, connection))
                {
                    DataTable dataTable = new DataTable();
                    adapter.Fill(dataTable);

                    comboBox.DataSource = dataTable;
                    comboBox.DisplayMember = dataTable.Columns[1].ColumnName; // Отображаемое поле (название)
                    comboBox.ValueMember = dataTable.Columns[0].ColumnName;   // Значение (ID)
                    comboBox.SelectedIndex = -1; // Сбросить выбор
                }
            }
            catch (Exception ex)
            {
                MessageBox.Show($"Ошибка при заполнении списка: {ex.Message}", "Ошибка", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        /// <summary>
        /// Заполняет все ComboBox на форме справочными данными.
        /// </summary>
        private void LoadReferenceDataToComboBoxes()
        {
            FillComboBox("SELECT categoryId, categoryName FROM Category", cbCategorySearch);
            FillComboBox("SELECT categoryId, categoryName FROM Category", cbCategory);
            FillComboBox("SELECT categoryId, categoryName FROM Category", cbCategorySearch2);
            
            FillComboBox("SELECT countryId, countryName FROM Country", cbCountrySearch);
            FillComboBox("SELECT countryId, countryName FROM Country", cbCountry);
            FillComboBox("SELECT countryId, countryName FROM Country", cbCountrySearch2);
            
            FillComboBox("SELECT companyId, companyName FROM Company", cbCompanySearch);
            FillComboBox("SELECT companyId, companyName FROM Company", cbCompany);
            FillComboBox("SELECT companyId, companyName FROM Company", cbCompanySearch2);

            FillComboBox("SELECT managerId, lastName + ' ' + firstName AS FullName FROM Manager", cbManagerSearch2);
            
            // Заполняем ComboBox для должности
            cbPosSearch.Items.AddRange(new object[] { "Менеджер", "Продавец", "Кассир", "Бухгалтер", "Директор" });
            cbPos.Items.AddRange(new object[] { "Менеджер", "Продавец", "Кассир", "Бухгалтер", "Директор" });
        }

        // --- Обработчики событий для переключения вкладок ---

        private void tabControl_SelectedIndexChanged(object sender, EventArgs e)
        {
            switch (tabControl.SelectedIndex)
            {
                case 0: // Вкладка "Товары"
                    LoadProductsTab();
                    break;
                case 1: // Вкладка "Справочники"
                    LoadReferencesTab();
                    break;
                case 2: // Вкладка "Менеджеры"
                    LoadManagersTab();
                    break;
                case 3: // Вкладка "Продажи"
                    LoadSalesTab();
                    break;
                case 4: // Вкладка "Отчеты"
                    break;
            }
        }

        // --- Методы загрузки данных для каждой вкладки ---

        private void LoadProductsTab()
        {
            string query = @"
                SELECT 
                    p.productId AS [ID],
                    c.companyName AS [Компания],
                    co.countryName AS [Страна],
                    cat.categoryName AS [Категория],
                    p.model AS [Модель],
                    p.color AS [Цвет],
                    p.numSIM AS [Кол-во SIM],
                    p.character AS [Характеристики],
                    p.price AS [Цена],
                    p.quantity AS [Кол-во на складе]
                FROM Product p
                LEFT JOIN Company c ON p.companyId = c.companyId
                LEFT JOIN Country co ON p.countryId = co.countryId
                LEFT JOIN Category cat ON p.categoryId = cat.categoryId";
            BindDataToGrid(query, dgvProduct);
        }

        private void LoadReferencesTab()
        {
            // По умолчанию загружаем категории
            if (cbSpravoch.SelectedIndex == -1) cbSpravoch.SelectedIndex = 0;
            else LoadSelectedReference(); // Если индекс уже установлен, загружаем выбранный справочник
        }

        private void LoadManagersTab()
        {
            string query = "SELECT managerId AS [ID], lastName AS [Фамилия], firstName AS [Имя], fatherName AS [Отчество], position AS [Должность] FROM Manager";
            BindDataToGrid(query, dgvManager);
        }

        private void LoadSalesTab()
        {
            string query = @"
                SELECT 
                    pur.purchaseId AS [ID],
                    cat.categoryName AS [Категория],
                    co.countryName AS [Страна],
                    c.companyName AS [Компания],
                    p.model AS [Модель],
                    pur.purchasePrice AS [Цена],
                    m.lastName + ' ' + m.firstName AS [Менеджер],
                    pur.count AS [Кол-во],
                    (pur.purchasePrice * pur.count) AS [Стоимость],
                    pur.datePurchase AS [Дата]
                FROM Purchase pur
                LEFT JOIN Product p ON pur.productId = p.productId
                LEFT JOIN Category cat ON p.categoryId = cat.categoryId
                LEFT JOIN Country co ON p.countryId = co.countryId
                LEFT JOIN Company c ON p.companyId = c.companyId
                LEFT JOIN Manager m ON pur.managerId = m.managerId";
            BindDataToGrid(query, dgvPurchase);
        }

        // --- Обработчики событий для элементов управления ---

        // Вкладка "Справочники"
        private void cbSpravoch_SelectedIndexChanged(object sender, EventArgs e)
        {
            LoadSelectedReference();
        }

        private void LoadSelectedReference()
        {
            string query = "";
            switch (cbSpravoch.SelectedIndex)
            {
                case 0: // Категории
                    query = "SELECT categoryId AS [ID], categoryName AS [Название] FROM Category";
                    break;
                case 1: // Компании
                    query = "SELECT companyId AS [ID], companyName AS [Название] FROM Company";
                    break;
                case 2: // Страны
                    query = "SELECT countryId AS [ID], countryName AS [Название] FROM Country";
                    break;
            }
            if (!string.IsNullOrEmpty(query))
            {
                BindDataToGrid(query, dgvSpravoch);
            }
        }

        // Вкладка "Товары" - Поиск
        private void btnSearch_Click(object sender, EventArgs e)
        {
            string query = @"
                SELECT 
                    p.productId AS [ID],
                    c.companyName AS [Компания],
                    co.countryName AS [Страна],
                    cat.categoryName AS [Категория],
                    p.model AS [Модель],
                    p.color AS [Цвет],
                    p.numSIM AS [Кол-во SIM],
                    p.character AS [Характеристики],
                    p.price AS [Цена],
                    p.quantity AS [Кол-во на складе]
                FROM Product p
                LEFT JOIN Company c ON p.companyId = c.companyId
                LEFT JOIN Country co ON p.countryId = co.countryId
                LEFT JOIN Category cat ON p.categoryId = cat.categoryId
                WHERE 1=1";

            if (chbCategory.Checked && cbCategorySearch.SelectedValue != null)
                query += $" AND cat.categoryId = {cbCategorySearch.SelectedValue}";
            if (chbCountry.Checked && cbCountrySearch.SelectedValue != null)
                query += $" AND co.countryId = {cbCountrySearch.SelectedValue}";
            if (chbCompany.Checked && cbCompanySearch.SelectedValue != null)
                query += $" AND c.companyId = {cbCompanySearch.SelectedValue}";
            if (chbSIM.Checked)
                query += $" AND p.numSIM >= {numSIMSearch.Value}";
            if (chbPrice.Checked)
                query += $" AND p.price <= {numPriceSearch.Value}";

            BindDataToGrid(query, dgvProduct);
        }

        private void chbExit_CheckedChanged(object sender, EventArgs e)
        {
            if (chbExit.Checked)
            {
                LoadProductsTab();
                chbExit.Checked = false;
            }
        }
        
        // Вкладка "Менеджеры" - Поиск
        private void searchManager_TextChanged(object sender, EventArgs e)
        {
             string query = "SELECT managerId AS [ID], lastName AS [Фамилия], firstName AS [Имя], fatherName AS [Отчество], position AS [Должность] FROM Manager WHERE 1=1";

            if (!string.IsNullOrWhiteSpace(txtFIOSearch.Text))
            {
                query += $" AND (lastName LIKE '%{txtFIOSearch.Text}%' OR firstName LIKE '%{txtFIOSearch.Text}%')";
            }
            if (cbPosSearch.SelectedItem != null)
            {
                query += $" AND position = '{cbPosSearch.SelectedItem}'";
            }

            BindDataToGrid(query, dgvManager);
        }

        private void chBExit2_CheckedChanged(object sender, EventArgs e)
        {
            if (chBExit2.Checked)
            {
                LoadManagersTab();
                chBExit2.Checked = false;
            }
        }

        // Вкладка "Продажи" - Поиск
        private void btnSearchSales_Click(object sender, EventArgs e)
        {
            string query = @"
                SELECT 
                    pur.purchaseId AS [ID],
                    cat.categoryName AS [Категория],
                    co.countryName AS [Страна],
                    c.companyName AS [Компания],
                    p.model AS [Модель],
                    pur.purchasePrice AS [Цена],
                    m.lastName + ' ' + m.firstName AS [Менеджер],
                    pur.count AS [Кол-во],
                    (pur.purchasePrice * pur.count) AS [Стоимость],
                    pur.datePurchase AS [Дата]
                FROM Purchase pur
                LEFT JOIN Product p ON pur.productId = p.productId
                LEFT JOIN Category cat ON p.categoryId = cat.categoryId
                LEFT JOIN Country co ON p.countryId = co.countryId
                LEFT JOIN Company c ON p.companyId = c.companyId
                LEFT JOIN Manager m ON pur.managerId = m.managerId
                WHERE 1=1";

            if (datel.Value != null && date2.Value != null)
            {
                query += $" AND pur.datePurchase BETWEEN '{datel.Value:yyyyMMdd}' AND '{date2.Value:yyyyMMdd}'";
            }
            if (chbCategory2.Checked && cbCategorySearch2.SelectedValue != null)
                query += $" AND cat.categoryId = {cbCategorySearch2.SelectedValue}";
            if (chbCountry2.Checked && cbCountrySearch2.SelectedValue != null)
                query += $" AND co.countryId = {cbCountrySearch2.SelectedValue}";
            if (chbCompany2.Checked && cbCompanySearch2.SelectedValue != null)
                query += $" AND c.companyId = {cbCompanySearch2.SelectedValue}";
            if (chbManager2.Checked && cbManagerSearch2.SelectedValue != null)
                query += $" AND m.managerId = {cbManagerSearch2.SelectedValue}";
            if (chbPrice2.Checked)
                query += $" AND pur.purchasePrice <= {numPriceSearch2.Value}";

            BindDataToGrid(query, dgvPurchase);
        }

        private void chbExit3_CheckedChanged(object sender, EventArgs e)
        {
            if (chbExit3.Checked)
            {
                LoadSalesTab();
                chbExit3.Checked = false;
            }
        }
        
        // Вкладка "Отчеты"
        private void btnGenerateReport_Click(object sender, EventArgs e)
        {
            string query = "";
            string periodCondition = "";
            if (date3.Value != null && date4.Value != null)
            {
                periodCondition = $"AND pur.datePurchase BETWEEN '{date3.Value:yyyyMMdd}' AND '{date4.Value:yyyyMMdd}'";
            }

            if (rbProduct.Checked)
            {
                query = $@"
                    SELECT p.model AS [Товар], SUM(pur.count) AS [Продано шт.], SUM(pur.purchasePrice * pur.count) AS [Выручка]
                    FROM Purchase pur JOIN Product p ON pur.productId = p.productId
                    WHERE 1=1 {periodCondition}
                    GROUP BY p.model ORDER BY [Выручка] DESC";
            }
            else if (rbCategory.Checked)
            {
                query = $@"
                    SELECT cat.categoryName AS [Категория], SUM(pur.count) AS [Продано шт.], SUM(pur.purchasePrice * pur.count) AS [Выручка]
                    FROM Purchase pur JOIN Product p ON pur.productId = p.productId JOIN Category cat ON p.categoryId = cat.categoryId
                    WHERE 1=1 {periodCondition}
                    GROUP BY cat.categoryName ORDER BY [Выручка] DESC";
            }
            else if (rbCountry.Checked)
            {
                query = $@"
                    SELECT co.countryName AS [Страна], SUM(pur.count) AS [Продано шт.], SUM(pur.purchasePrice * pur.count) AS [Выручка]
                    FROM Purchase pur JOIN Product p ON pur.productId = p.productId JOIN Country co ON p.countryId = co.countryId
                    WHERE 1=1 {periodCondition}
                    GROUP BY co.countryName ORDER BY [Выручка] DESC";
            }
            else if (rbCompany.Checked)
            {
                query = $@"
                    SELECT c.companyName AS [Производитель], SUM(pur.count) AS [Продано шт.], SUM(pur.purchasePrice * pur.count) AS [Выручка]
                    FROM Purchase pur JOIN Product p ON pur.productId = p.productId JOIN Company c ON p.companyId = c.companyId
                    WHERE 1=1 {periodCondition}
                    GROUP BY c.companyName ORDER BY [Выручка] DESC";
            }
            else if (rbManager.Checked)
            {
                query = $@"
                    SELECT m.lastName + ' ' + m.firstName AS [Менеджер], SUM(pur.count) AS [Продано шт.], SUM(pur.purchasePrice * pur.count) AS [Выручка]
                    FROM Purchase pur JOIN Manager m ON pur.managerId = m.managerId
                    WHERE 1=1 {periodCondition}
                    GROUP BY m.lastName, m.firstName ORDER BY [Выручка] DESC";
            }

            if (!string.IsNullOrEmpty(query))
            {
                BindDataToGrid(query, dgvReport);
            }
        }
    }
}
