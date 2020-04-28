#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import math
import random
import io
import os
import sys
import numpy as np 
import ujson as json
import signal
import argparse
##辅助画图导入
from matplotlib import pyplot as plt
import mplfinance 
from matplotlib.pylab import date2num,num2date
import datetime
import time
from ipabase import get_uuid_str,easylog,send_dingding_msg,second2_str24h
from comm.comm import (ORDER_STATUS_FILLED,ORDER_SIDE_BUY,ORDER_SIDE_SELL)
#from findex.findex import (MMMBOS_PAIRID as TARGET_PID,
#                           TEST_ACT1 as HOST_ACT,
#                    create_order,cancel_order,query_pair_book,query_unfilled_orders,query_filled_orders)
from hoo.hoo import (TARGET_PID, MIN_TOKEN_AMOUNT,
                    TEST_ACT1 as HOST_ACT,
                    init_hooclient,
                    create_order,cancel_order,query_pair_book,query_unfilled_orders,query_filled_orders)

PRICE_RNUM = 8

def random_n():
    ## E=0.125
    return random_n_basic()*random.random()

def random_n_basic():
    a=random.random()
    return (1-math.pow(a,1/3.0))

def random_normal(mu=0.5,sigma=0.2):
    sampleNo = 1
    np.random.seed(random.randint(1,100000000))
    x=np.random.normal(mu, sigma, sampleNo)
    while(x[0]<0 or x[0]>1):
        np.random.seed(random.randint(1,100000000))
        x=np.random.normal(mu, sigma, sampleNo)
    return x[0]

def random_beta(a,b):
    return np.random.beta(a,b,(1,1))[0][0]

#原则上是高于控制线 挂卖追买，低于控制线 挂买追卖

######
# 设置边界区域


self_buy_list=[]
self_sell_list=[]

total_sell=0
total_sell_value=0
total_profits=0
total_count=0

#max,min,aim,time,number
days=[0,0,1,0,0]
mins_1=[0,0,1,0,0]
mins_5=[0,0,1,0,0]
mins_15=[0,0,1,0,0]
mins_30=[0,0,1,0,0]
hours=[0,0,1,0,0]
hours_4=[0,0,1,0,0]

u_k=0
new_sgin=0
buy_price=0
V_=0.4


def calculate_rebuy_rate(total_money,profits):
    """
    计算复投率
    """
    if profits/total_money>15:
        return 0.4
    if profits/total_money>10:
        return 0.5
    if profits/total_money>1:
        return 0.6
    if profits/total_money>0.5:
        return 0.7
    if profits/total_money>0.3:
        return 0.9
    return 1

def calculate_u(allocate_rate,price_rate,profits,count,rb,old_money,V,alpha,old_u,E,a):
    """

    """
    pp=profits-alpha*V*count
    if pp<0:
        pp=0
    total_money=pp+old_money
    

    ds=old_u*(1-1/(1+a*2))*random.random()
    l=old_u-ds
    # while(l*1.02<u0):
    #     ds=u0*(1-1/1.02)*random.random()
    #     l=u0-ds
    tms=0
    while(True):
        c=total_money/E/l
        total_px=0
        xc=0
        for i in range(len(allocate_rate)):
            ar=allocate_rate[i]
            pr=price_rate[i]*l
            if pr>V:
                tp=(pr-V)*ar*c
                total_px=total_px+tp
                xc+=1
            else:
                break

        if xc==len(allocate_rate):
            if profits*(1-rb)>old_money:
                return l
        else:
            if total_px<=pp:
                return l
            
        l=l*0.99
        tms=tms+1
        if tms>100000:
            easylog.error("calculate_u profits:%s,count:%s", profits, count)
            return l
    return l

def allocate_D2(allocate_rate,price_rate,L,total_money,E):
    m=total_money
    c=total_money/E/L
    l=L
    d=[]
    bp=[]
    for i in range(len(price_rate)):
        l=L*price_rate[i]
        f=round(l,PRICE_RNUM)
        m=m-f
        d.append(f)
    for i in range(len(allocate_rate)):
        v=math.floor(allocate_rate[i]*c)
        m=m-v*d[i]
        bp.append((v+1,d[i]))
    return bp,m

def allocate_D1(allocate_rate_D2,price_rate_D2,allocate_count_D2,price_rate_D1,profits,count,rb,old_money,V,alpha,L,a):
    """
    返回 上边界U，分配比例
    """
    U_=L*(1+a*2*(1-random_n()))
    if (U_-L)/L<0.005:
        U_=L*(1+a)
    x=0
    count_=count
    profits_=profits
    D1=[]
    for i in range(len(price_rate_D1)):
        x=calculate_count(U_,L,profits_,count_,price_rate_D1,allocate_rate_D2,allocate_count_D2,price_rate_D2,old_money,V,alpha,i,a)
        D1.append((x,round(U_*price_rate_D1[i],PRICE_RNUM)))
        count+=x
        if U_*price_rate_D1[i]-V*alpha>0:
            profits_+=x*(U_*price_rate_D1[i]-V*alpha)
    return D1

def calculate_count(price_u,L,old_profits,old_count,price_rate_D1,allocate_rate_D2,allocate_count_D2,price_rate_D2,old_money,V,alpha,step,a):
    l=L+(price_rate_D1[step]*1.01-price_rate_D1[0])*price_u
    # l=price_u*price_rate_D1[step]*1.01-ds
    if step<len(price_rate_D1)-1:
        l=L+(price_rate_D1[step+1]-price_rate_D1[0])*price_u
    x=allocate_count_D2[step]*((price_rate_D2[step]*l)/(price_rate_D1[step]*price_u)*(1+random_normal()))
    if price_u<V:
        x=allocate_count_D2[step]*((price_rate_D2[step]*l)/(price_rate_D1[step]*price_u)*random_normal())
    price=price_u*price_rate_D1[step]
    tms=0
    while(True):
        xc=0
        profits1=x*(price-V*alpha)
        if profits1<0:
            profits1=0
        profits=profits1+old_profits

        ## 计算E
        sm=0
        for i in range(len(allocate_rate_D2)):
            sm+=allocate_rate_D2[i]*l*math.pow(1/(1+a),i)
        rb=calculate_rebuy_rate(old_money,profits)
        real_money=rb*profits+old_money
        c=real_money/sm
        total_px=0

        for i in range(len(allocate_rate_D2)):
                ar=allocate_rate_D2[i]
                pr=l*math.pow(1/(1+a),i)
                if pr>V:
                    tp=(pr-V)*ar*c
                    total_px=total_px+tp
                    xc+=1
                else:
                    break
        
        if xc==len(allocate_rate_D2):
            if profits*(1-rb)>old_money*0.4:
                return x
        else:
            if total_px<=rb*profits:
                return x
            
        x=x*1.01
        tms=tms+1
        if tms>1000:
            easylog.error("calculate_count price_u:%s,L:%s,old_money:%s", price_u, L, old_money)
            return x

def calculate_D1_D2(old_money,profits,count,U_value,V,a=0.01,alpha=0.8):
    """
    old_money 原始的1W美金
    profits 总利润
    count 总销售量
    U_value 真实买家卖单上边界
    V 价值控制线
    alpha 价值波动系数
    """
    d=[]
    bp=[]
    # 判断profits 
    rb=calculate_rebuy_rate(old_money,profits)
    pp=profits-alpha*V*count
    if pp<0:
        pp=0
    allocate_rate=get_initial_allocate_rate(1.18,[(6,0),(2.2,1)])
    price_rate=[]
    price_rate_u=[]
    qq=1
    e=0
    allocate_count_D2=[]
    
    for i in range(27):
        e+=qq*allocate_rate[i]
        price_rate.append(qq)
        qq=qq/(1+a)
        qq=qq*(1+random_n()*a)
    pt=pp*rb+old_money
    easylog.info("allocate_rate:%s,price_rate:%s,profits:%s,count:%s,rb:%s,old_money:%s,V:%s,alpha:%s,U_value:%s,e:%s",
                    allocate_rate,price_rate,profits,count,rb,old_money,V,alpha,U_value,e)
    l=calculate_u(allocate_rate,price_rate,profits,count,rb,old_money,V,alpha,U_value,e,a)
    D2,residue=allocate_D2(allocate_rate,price_rate,l,pt,e)
    qu=1
    for i in range(27):
        price_rate_u.append(qu)
        qu=qu*(1+a)
        qu=qu*(1-random_n()*a)
        allocate_count_D2.append(D2[i][0])

    D1=allocate_D1(allocate_rate,price_rate,allocate_count_D2,price_rate_u,profits,count,rb,old_money,V,alpha,l,a)

    return D1,D2
#setD2(10000,1)

def get_initial_allocate_rate(m,t,number=27):
    #1.66,10,70
    #1.3,0,10
    sumx=0
    sd=[]
    for i in range(number):
        sumx_=math.pow(m,i+1)
        for i1 in range(len(t)):
            sumx_+=t[i1][0]*math.pow(i+1,t[i1][1])
        sd.append(sumx_)
        sumx+=sumx_
    ld=[]
    for i in range(number):
        ld.append(sd[i]/sumx)
    return ld

def reallocate(n,u,l):
    reset_days(0,n,u-l)

def get_u_l(u,l,u_,l_,length,n,old):
    global days
    global hours_4
    global hours
    global mins_30
    global mins_15
    global mins_5
    global mins_1
    if u_==0:
        x=l+random.random()*(u-l)
        if old>-1:
            easylog.debug("====== NOW TIME ====== u:%s, l:%s, u_:%s, l_:%s, length:%s, n:%s, old:%s",u,l,u_,l_,length,n,old)
            x1=old*(0.9995+0.001*random.random())
            if x1<u and x1>l:
                x=x1
        set_basic_data(x)
        return False,x,x
    tm=(length-(u_-l_)) 
    _l=l_-tm
    _u=u_+tm
    if _u<l or _l>u:
        reallocate(n,u,l)
        if n==6:
            dates_point=days
        if n==5:
            dates_point=hours_4
        if n==4:
            dates_point=hours
        if n==3:
            dates_point=mins_30
        if n==2:
            dates_point=mins_15
        if n==1:
            dates_point=mins_5
        if n==0:
            dates_point=mins_1   
        return get_u_l(u,l,dates_point[0],dates_point[1],dates_point[2],n,old)
    _l=_l if _l>l else l
    _u=_u if _u<u else u
    return True,_l,_u

def set_basic_data(x):
    global days
    global hours_4
    global hours
    global mins_30
    global mins_15
    global mins_5
    global mins_1
    if days[0]==0:
        days[0]=x
        days[1]=x
    if mins_5[0]==0:
        mins_5[0]=x
        mins_5[1]=x
    if mins_1[0]==0:
        mins_1[0]=x
        mins_1[1]=x
    if mins_15[0]==0:
        mins_15[0]=x
        mins_15[1]=x
    if mins_30[0]==0:
        mins_30[0]=x
        mins_30[1]=x
    if hours[0]==0:
        hours[0]=x
        hours[1]=x
    if hours_4[0]==0:
        hours_4[0]=x
        hours_4[1]=x

    if x>mins_1[0] :
        mins_1[0]=x
    if x< mins_1[1]:
        mins_1[1]=x
    if x>mins_5[0] :
        mins_5[0]=x
    if x< mins_5[1]:
        mins_5[1]=x
    if x>mins_15[0] :
        mins_15[0]=x
    if x< mins_15[1]:
        mins_15[1]=x
    if x>mins_30[0] :
        mins_30[0]=x
    if x< mins_30[1]:
        mins_30[1]=x
    if x>hours[0] :
            hours[0]=x
    if x< hours[1]:
            hours[1]=x
    if x>hours_4[0] :
            hours_4[0]=x
    if x< hours_4[1]:
            hours_4[1]=x
    if x>days[0] :
            days[0]=x
    if x< days[1]:
            days[1]=x

def generate_k_line_basic(u,l,old=-1):
    global days
    global hours_4
    global hours
    global mins_30
    global mins_15
    global mins_5
    global mins_1
 
    flag,days_l,days_u=get_u_l(u,l,days[0],days[1],days[2],6,old)
    if not flag:
        return days_u

    flag,hours_4_l,hours_4_u=get_u_l(days_u,days_l,hours_4[0],hours_4[1],hours_4[2],5,old)
    if not flag:
        return hours_4_u

    flag,hours_l,hours_u=get_u_l(hours_4_u,hours_4_l,hours[0],hours[1],hours[2],4,old)
    if not flag:
        return hours_u

    flag,mins_30_l,mins_30_u=get_u_l(hours_u,hours_l,mins_30[0],mins_30[1],mins_30[2],3,old)
    if not flag:
        return mins_30_u

    flag,mins_15_l,mins_15_u=get_u_l(mins_30_u,mins_30_l,mins_15[0],mins_15[1],mins_15[2],2,old)
    if not flag:
        return mins_15_u
    
    flag,mins_5_l,mins_5_u=get_u_l(mins_15_u,mins_15_l,mins_5[0],mins_5[1],mins_5[2],1,old)
    if not flag:
        return mins_5_u
    
    flag,mins_1_l,mins_1_u=get_u_l(mins_5_u,mins_5_l,mins_1[0],mins_1[1],mins_1[2],1,old)
    if not flag:
        return mins_1_u

    x=mins_1_l+random.random()*(mins_1_u-mins_1_l)
    set_basic_data(x)

    if u==x:
        easylog.info("flag_generate_x: old=%s \n days:%s \n hours_4:%s \n hours:%s \n mins_30:%s \n mins_15:%s \n mins_5:%s \n mins1_:%s \n",old,days,hours_4,hours,mins_30,mins_15,mins_5,mins_1)
        
    return x

def generate_k_line(u,l,tm,old):
    global days
    global hours_4
    global hours
    global mins_30
    global mins_15
    global mins_5
    global mins_1
    _u=u-l
    
    if  tm-days[3]>=1:
        reset_days(tm,6,_u)
        return generate_k_line_basic(u,l,old)
    if  (tm-hours_4[3])>=1/6:
        reset_days(tm,5,days[2])
        return generate_k_line_basic(u,l,old)
    if  (tm-hours[3])>=1/24:
        reset_days(tm,4,hours_4[2])
        return generate_k_line_basic(u,l,old)
    if  (tm-mins_30[3])>=1/48:
        reset_days(tm,3,hours[2])
        return generate_k_line_basic(u,l,old)
    if  (tm-mins_15[3])>=1/96:
        reset_days(tm,2,mins_30[2])
        return generate_k_line_basic(u,l,old)
    if  (tm-mins_5[3])>=1/288:
        reset_days(tm,1,mins_15[2])
        return generate_k_line_basic(u,l,old)
    if  (tm-mins_1[3])>=1/1440:
        reset_days(tm,0,mins_5[2])
        return generate_k_line_basic(u,l,old)
    return generate_k_line_basic(u,l)

def reset_days(tm,lv,_u):
    global days
    global hours_4
    global hours
    global mins_30
    global mins_15
    global mins_5
    global mins_1

    if lv >5:
        k=random_beta(15,24)*0.6+0.4
        k=k*_u
        if tm>0:
            days=[0,0,k,now_to_day_number(),0]
        else:
            tn=days[3]
            days=[0,0,k,tn,0]
    
    if lv >4:
        k=(random_beta(15,24)*0.5+0.5)*days[2]
        if k<0.31*_u:
            k=0.31*_u

        if tm>0:
            hours_4=[0,0,k,now_to_hours_4_number(),0]
        else:
            tn=hours_4[3]
            hours_4=[0,0,k,tn,0]
    
    if lv >3:
        k=(random_beta(4,7.5)*0.6+0.4)*hours_4[2]
        if k<0.15*_u:
            k=0.15*_u
        if tm>0:
            hours=[0,0,k,now_to_hours_number(),0]
        else:
            tn=hours[3]
            hours=[0,0,k,tn,0]
    
    if lv >2:
        k=(random_beta(3.8,7.3)*0.7+0.3)*hours[2]
        if k<0.07*_u:
            k=0.07*_u

        if tm>0:
            mins_30=[0,0,k,now_to_mins_30_number(),0]
        else:
            tn=mins_30[3]
            mins_30=[0,0,k,tn,0]
    if lv >1:
        k=(random_beta(3.5,7)*0.7+0.3)*mins_30[2]
        if k<0.03*_u:
            k=0.03*_u
        if tm>0:
            mins_15=[0,0,k,now_to_mins_15_number(),0]
        else:
            tn=mins_15[3]
            mins_15=[0,0,k,tn,0]
    if lv >0:
        k=(random_beta(2.5,6.2)*0.8+0.2)*mins_15[2]
        if k<0.015*_u:
            k=0.015*_u
        if tm>0:
            mins_5=[0,0,k,now_to_mins_5_number(),0]
        else:
            tn=mins_5[3]
            mins_5=[0,0,k,tn,0]
    if lv >-1:
        k=(random_beta(2.5,6)*0.8+0.2)*mins_5[2]
        if k<0.007*_u:
            k=0.007*_u
        if tm>0:
            mins_1=[0,0,k,now_to_mins_1_number(),0]
        else:
            tn=mins_1[3]
            mins_1=[0,0,k,tn,0]
    easylog.debug("flag_generate_x: u=%s \n days:%s \n hours_4:%s \n hours:%s \n mins_30:%s \n mins_15:%s \n mins_5:%s \n mins1_:%s \n",_u,days,hours_4,hours,mins_30,mins_15,mins_5,mins_1)
        

def now_to_day_number():
    snow=datetime.datetime.now().strftime('%Y-%m-%d')
    tnow=datetime.datetime.strptime(snow,'%Y-%m-%d')
    return date2num(tnow)

def now_to_hours_4_number():
    snow=datetime.datetime.now().strftime('%Y-%m-%d')
    snow1=datetime.datetime.now().strftime('%Y-%m-%d %H')
    
    v=date2num(datetime.datetime.strptime(snow,'%Y-%m-%d'))
    tnow=date2num(datetime.datetime.strptime(snow1,'%Y-%m-%d %H'))-v
    x=math.floor(tnow*6)/6
    easylog.info("flag_before: %s,%s,",snow1,x)
    return x+v

def now_to_hours_number():
    snow=datetime.datetime.now().strftime('%Y-%m-%d %H')
    tnow=datetime.datetime.strptime(snow,'%Y-%m-%d %H')
    return date2num(tnow)

def now_to_mins_30_number():
    snow=datetime.datetime.now().strftime('%Y-%m-%d %H')
    snow1=datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    v=date2num(datetime.datetime.strptime(snow,'%Y-%m-%d %H'))
    tnow=date2num(datetime.datetime.strptime(snow1,'%Y-%m-%d %H:%M'))-v
    x=math.floor(tnow*48)/48
    return x+v
def now_to_mins_15_number():
    snow=datetime.datetime.now().strftime('%Y-%m-%d %H')
    snow1=datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    v=date2num(datetime.datetime.strptime(snow,'%Y-%m-%d %H'))
    tnow=date2num(datetime.datetime.strptime(snow1,'%Y-%m-%d %H:%M'))-v
    x=math.floor(tnow*96)/96
    return x+v
def now_to_mins_5_number():
    snow=datetime.datetime.now().strftime('%Y-%m-%d %H')
    snow1=datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    v=date2num(datetime.datetime.strptime(snow,'%Y-%m-%d %H'))
    tnow=date2num(datetime.datetime.strptime(snow1,'%Y-%m-%d %H:%M'))-v
    x=math.floor(tnow*288)/288
    return x+v
def now_to_mins_1_number():
    snow=datetime.datetime.now().strftime('%Y-%m-%d %H')
    snow1=datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    v=date2num(datetime.datetime.strptime(snow,'%Y-%m-%d %H'))
    tnow=date2num(datetime.datetime.strptime(snow1,'%Y-%m-%d %H:%M'))-v
    x=math.floor(tnow*1440)/1440
    return x+v



def take_second(elem):
    return elem[1]

def take_first(elem):
    return elem[0]

def cancel_all_orders():
    global self_unfilled_orders_buy,self_unfilled_orders_buy_helper
    for order_id in self_unfilled_orders_buy:
        ret = cancel_order(order_id, self_unfilled_orders_buy[order_id]['others'],HOST_ACT)
    self_unfilled_orders_buy, self_unfilled_orders_buy_helper = {}, []

def create_buy_orders(orders):
    global self_unfilled_orders_buy,self_unfilled_orders_buy_helper
    self_unfilled_orders_buy_helper.sort(key=take_first)
    for order in orders:
        if len(self_unfilled_orders_buy_helper)>0:
            orderc=self_unfilled_orders_buy_helper[0]
            order_id=orderc[1]
            others=self_unfilled_orders_buy[order_id]["others"]
            ret=cancel_order(order_id,others,HOST_ACT)
            
        ret=create_order(TARGET_PID, str(order[1]), str(order[0]), ORDER_SIDE_BUY, get_uuid_str(), HOST_ACT)
        self_unfilled_orders_buy[ret["order_id"]]={
            "price":order[1],
            "created_at":0,
            "amount":order[0],
            "filled_amount" :0,
            "filled_cash_amount" :0,
            'order_id': ret['order_id'],
            'others': ret['others']
        }
    for order_id in self_unfilled_orders_buy:
        self_unfilled_orders_buy_helper.append((self_unfilled_orders_buy[order_id]["price"],self_unfilled_orders_buy[order_id]["order_id"]))


def create_sell_orders(orders):
    global self_unfilled_orders_sell,self_unfilled_orders_sell_helper
    self_unfilled_orders_sell_helper.sort(key=take_first)
    for order in orders:
        if len(self_unfilled_orders_sell_helper)>0 :
            orderc=self_unfilled_orders_sell_helper[0]
            order_id=orderc[1]
            others=self_unfilled_orders_sell[order_id]["others"]
            ret=cancel_order(order_id,others,HOST_ACT)

        ret=create_order(TARGET_PID, str(order[1]), str(order[0]), ORDER_SIDE_SELL, get_uuid_str(), HOST_ACT)
        self_unfilled_orders_sell[ret["order_id"]]={
        "price":order[1],
        "created_at":0,
        "amount":order[0],
        "filled_amount" :0,
        "filled_cash_amount" :0,
        'order_id': ret['order_id'],
        'others': ret['others']
    }
    for record_id in self_unfilled_orders_sell:
        self_unfilled_orders_sell_helper.append((self_unfilled_orders_sell[record_id]["price"],self_unfilled_orders_sell[record_id]["order_id"]))
        
def cancel_order_for_back_buy(order_id):
    global back_buy_orders
    others=back_buy_orders[order_id]["others"]
    ret=cancel_order(order_id,others,HOST_ACT)
    if order_id in back_buy_orders:
        del back_buy_orders[order_id]


def create_sell_order(order):
    global unfilled_sell_1_order,sell_1

    if 'order_id' in unfilled_sell_1_order:
        order_id=unfilled_sell_1_order["order_id"]
        others=unfilled_sell_1_order["others"]
        ret=cancel_order(order_id,others,HOST_ACT)

    ret=create_order(TARGET_PID, str(order[1]), str(order[0]), ORDER_SIDE_SELL, get_uuid_str(), HOST_ACT)
    sell_1.add(ret['order_id'])
    unfilled_sell_1_order={
        "price":order[1],
        "created_at":0,
        "amount":order[0],
        "filled_amount" :0,
        "filled_cash_amount" :0,
        'order_id': ret['order_id'],
        'others': ret['others']
    }
    return ret['order_id']

def cancel_privous_1_order():
    global unfilled_buy_1_order, unfilled_sell_1_order
    if unfilled_sell_1_order:
        order_id=unfilled_sell_1_order["order_id"]
        others=unfilled_sell_1_order["others"]
        ret=cancel_order(order_id,others,HOST_ACT)
    if unfilled_buy_1_order:
        order_id=unfilled_buy_1_order["order_id"]
        others=unfilled_buy_1_order["others"]
        ret=cancel_order(order_id,others,HOST_ACT)

def create_buy_order(order,status=False):
    global unfilled_buy_1_order,buy_1,back_buy_orders

    if "order_id" in unfilled_buy_1_order:
        order_id=unfilled_buy_1_order["order_id"]
        others=unfilled_buy_1_order["others"]
        ret=cancel_order(order_id,others,HOST_ACT)

    ret=create_order(TARGET_PID, str(order[1]), str(order[0]), ORDER_SIDE_BUY, get_uuid_str(), HOST_ACT)
    buy_1.add(ret['order_id'])

    unfilled_buy_1_order={
        "price":order[1],
        "created_at":0,
        "amount":order[0],
        "filled_amount" :0,
        "filled_cash_amount" :0,
        'order_id': ret['order_id'],
        'others': ret['others']
    }
    if status:
        back_buy_orders[ret['order_id']]=unfilled_buy_1_order

    return ret['order_id']

self_unfilled_orders_sell={}
self_filled_orders_sell={}
self_unfilled_orders_buy={}
self_filled_orders_buy={}
unfilled_orders={}
self_unfilled_orders={}
orders_helper={}
self_unfilled_orders_sell_helper=[]
self_filled_orders_sell_helper=[]
self_unfilled_orders_buy_helper=[]
self_filled_orders_buy_helper=[]
unfilled_orders_helper=[]
self_unfilled_orders_helper=[]
unfilled_buy_1_order={}
unfilled_sell_1_order={}
back_buy_orders={}
back_sell_orders={}
buy_1=set()
sell_1=set()

def delete_list(item,ls,i=0):
    l=None
    for v in ls:
        if item ==v[i]:
            l=v
            break
    if l!=None:
        ls.remove(v)

def calculate_back_count(total,week_max,day_max,hour_max,week_spend,day_spend,hour_spend):
    speed=0
    day_normal=week_max/7
    hour_normal=day_max/24
    speed=day_normal/24
    if total<(week_max-week_spend):
        speed=hour_normal
    if total<(day_max-day_spend):
        speed=day_normal
    if total<(hour_max-hour_spend):
        speed=hour_max
    canspend=speed-hour_spend
    return max(canspend,0)
def generate_buy(week_end,day_end,hour_end,now_time,canspend,hour_spend,interval):
    redidual=hour_end-now_time
    times=math.floor(redidual/interval/5)
    if times==0:
        return canspend
    r=random.random()
    if r<=0.2:
        return random_normal(1/times,0.3/times)*canspend
    return 0

g_config = {}
g_config_file = None
def reload_robot_config(signum, frame):
    # {
    #   "accept_price":0.8033, 
    #   "hour_max_buy":3000.10,
    #   "day_max_buy":3000.10 * 10,
    #   "week_max_buy": 3000.10 * 10 * 3,
    #   "market_check_interval_sec" : 6.3,
    #   "buy1_sell1_interval_sec" : 0.01,
    #   "whole_depth_money" : 10000,
    #   "oneday_min_volume" : 3500000
    # }
    global g_config, V_, g_config_file
    easylog.info("starting to reload config with signal:%d", signum)
    with open(g_config_file) as fp:
        tmp = json.loads(fp.read())
        g_config.update(tmp)
    V_ = g_config['accept_price']

def check_include_by_price(price):
    global self_unfilled_orders_sell_helper
    for item in self_unfilled_orders_sell_helper:
        d=abs( item[0]-price)
        if  d<abs(price)*0.0000001 and d<0.0000001:
            return True
    return False


def calculate_depth(depth):
    """
    sell_edge,self_edge,sum_d,under_v,D_l
    """
    easylog.info("depth:%s", depth)
    global self_unfilled_orders_sell_helper,V
    D_l=sys.maxsize
    under_v=[]
    sum_d=0
    sell_edge=sys.maxsize #其他人卖的下边界
    self_edge=sys.maxsize
    for item in self_unfilled_orders_sell_helper:
        self_edge=min(item[0],self_edge)
    for d in depth:
        dprice=float(d["price"])
        if not check_include_by_price(dprice):
            sell_edge=min(sell_edge,dprice)
        D_l=min(dprice,D_l)
        if dprice<self_edge:
            amount=(float(d["amount"]))
            under_v.append((amount,dprice))
            sum_d+=amount*dprice
    easylog.info("sell_edge:%s self_edge:%s sum_d:%s under_v:%s D_l:%s", sell_edge,self_edge,sum_d,under_v,D_l)
    return sell_edge,self_edge,sum_d,under_v,D_l

def calculate_buy_edge(depth):
    max_buy=0
    count=0
    for d in depth:
        dprice=float(d["price"])
        if dprice <= max_buy:
            continue
        max_buy, count = dprice,float(d["amount"])
    return max_buy,count



def store_data(profits,count,spend,spend_c):
    easylog.debug('flag1:filled store_data: profits,count,spend,spend_c %s %s %s %s', profits,count,spend,spend_c)
    with open('./robot_data.json',"w") as file: 
        file.write(json.dumps({"profits":profits,"count":count,"spend":spend,"spend_c":spend_c}))
    
def load_data():
    if not os.path.exists('./robot_data.json'):
        return None
    with open('./robot_data.json','r') as f:
        return json.loads(f.read())


def setup_env():
    unfilled_orders_ = query_unfilled_orders(TARGET_PID, HOST_ACT)
    easylog.info("Going to cancel unfilled_orders_ count:%d", len(unfilled_orders_['rows']))
    for row in unfilled_orders_["rows"]:
        ret=cancel_order(row["order_id"],{'trade_no':row["trade_no"]},HOST_ACT)
    if len(unfilled_orders_["rows"])>0:
        sltime = 6.0
        easylog.info("will sleep %f seconds to wait cancel order ", sltime)
        time.sleep(sltime)


def main_process(conf_file):
    global V_,self_unfilled_orders_sell,self_filled_orders_sell,self_unfilled_orders_buy,self_filled_orders_buy
    global unfilled_orders,self_unfilled_orders,orders_helper,self_unfilled_orders_sell_helper,self_unfilled_orders_buy_helper
    global g_config, g_config_file
    global days
    global mins_1

    g_config_file = conf_file
    reload_robot_config(1, None)
    if not init_hooclient(g_config['dbfile'], g_config['client_id'], g_config['client_key'], g_config['dding_atoken'], g_config['bos_pairid']):
        easylog.error('Failed to init_hooclient()')
        return

    D1=[]
    D2=[]
    old_x=0

    back_start_time=0
    analyse_circle_startime=0
    self_buy_delimited=0
    back_pow=0
    profits=0
    count=0
    spend=0
    spend_c=0

    jd=load_data()
    if jd:
        profits=jd["profits"]
        count=jd["count"]
        spend=jd["spend"]
        spend_c=jd["spend_c"]
    
    time_circle=date2num(datetime.datetime.now())
    up_count=0
    up_spend_c=0
    #U_value=0.04
    #V_=0.005633
    U_value= 1.02 * V_
    tm=date2num(datetime.datetime.now())
    week_end=tm+7
    day_end=tm+1
    hour_end=tm+1/24
    week_spend=0
    day_spend=0
    hour_spend=0
    old_self_sell_max=10000
    old_self_buy_min=sys.maxsize
    t_spend=0
    t_profits=0
    t_spend_c=0
    t_profits_C=0
    hit_hour_max_check_wait_interval=2.0
    D1_l=0
    D_l=0
    self_buy_under_V=0
    distance=0
    model=0

    update_D=True
    setup_env()

    while True :
        try:
        ##查询orders
            unfilled_orders_ = query_unfilled_orders(TARGET_PID, HOST_ACT)
            filled_orders_=query_filled_orders(TARGET_PID, HOST_ACT)
            tm=date2num(datetime.datetime.now())
            buy_sell_1_cash_amount = [0,0]   # calculate the buy&sell first order dealed by others instead of robot
            buy_sell_1_amount = [0,0]        # 0: buy, 1: sell

        ## 分析 orders

        #1.计算profits 和count 同时找出 下探点

            profits_=0
            count_=0
            spend_=0
            spend_c_=0
            self_sell_price_min=0
            self_buy_min=sys.maxsize

            rows=unfilled_orders_["rows"]

            for row in rows:
                if row["side"]=='sell':
                    if row["order_id"] in sell_1:
                        buy_sell_1_cash_amount[1] = float(row["filled_cash_amount"])
                        buy_sell_1_amount[1]=float(row["filled_amount"])
                        continue
                    if row["order_id"] in self_unfilled_orders_sell.keys():
                        old_record=self_unfilled_orders_sell[row["order_id"]]
                        filled_amount=float(row["filled_amount"])
                        filled_cash_amount=float(row["filled_cash_amount"])
                        D1_l=min(D1_l,float(row["price"]))
                        if filled_cash_amount>old_record["filled_cash_amount"]:
                            profits_+=filled_cash_amount-old_record["filled_cash_amount"]
                            count_+=filled_amount-old_record["filled_amount"]
                            easylog.debug("flag1:unfilled sell %s filled_cash_amount:%s old_filled_cash_amount:%s,profits_:%s,count_:%f", 
                                            row["order_id"], filled_cash_amount,old_record["filled_cash_amount"],profits_,count_)
                            self_unfilled_orders_sell[row["order_id"]]["filled_cash_amount"]=filled_cash_amount
                            self_unfilled_orders_sell[row["order_id"]]["filled_amount"]=filled_amount
                            self_sell_price_min=max(self_sell_price_min,float(row["price"]))
                    else:
                        #ret = cancel_order(row["order_id"],{'trade_no':row["trade_no"]},HOST_ACT)
                        easylog.info("unfilled SELL Order not exist，order:%s", row)
                else:
                    if row["order_id"] in buy_1:
                        buy_sell_1_cash_amount[0]=  float(row["filled_cash_amount"])
                        buy_sell_1_amount[0]=float(row["filled_amount"])
                        if row["order_id"] in back_buy_orders:
                            cancel_order_for_back_buy(row["order_id"])
                        continue
                    if row["order_id"] in self_unfilled_orders_buy.keys():
                        old_record=self_unfilled_orders_buy[row["order_id"]]
                        filled_amount=float(row["filled_amount"])
                        filled_cash_amount=float(row["filled_cash_amount"])
                        if filled_cash_amount>old_record["filled_cash_amount"]:
                            spend_+=filled_cash_amount-old_record["filled_cash_amount"]
                            spend_c_+=filled_amount-old_record["filled_amount"]
                            easylog.debug("flag1:unfilled no-sell %s filled_cash_amount:%s old_filled_cash_amount:%s,spend_:%s,spend_c_:%f", 
                                            row["order_id"], filled_cash_amount,old_record["filled_cash_amount"],spend_,spend_c_)
                            self_unfilled_orders_buy[row["order_id"]]["filled_cash_amount"]=filled_cash_amount
                            self_unfilled_orders_buy[row["order_id"]]["filled_amount"]=filled_amount
                            self_buy_min=min(self_buy_min,float(row["price"]))

                    else:
                        #ret = cancel_order(row["order_id"],{'trade_no':row["trade_no"]},HOST_ACT)
                        easylog.info("unfilled BUY Order not exist，order:%s", row)

            rows=filled_orders_["rows"]
            for row in rows:
                if row["status"]=='canceled':
                    if row['order_id'] in self_unfilled_orders_buy.keys():
                        del self_unfilled_orders_buy[row['order_id']]
                        delete_list(row['order_id'],self_unfilled_orders_buy_helper,1)
                    elif row['order_id'] in self_unfilled_orders_sell.keys():
                        del self_unfilled_orders_sell[row["order_id"]]
                        delete_list(row["order_id"],self_unfilled_orders_sell_helper,1)
                    continue

                if row["side"]=='sell':
                    if row["order_id"] in sell_1:
                        buy_sell_1_cash_amount[1]=  float(row["filled_cash_amount"])
                        buy_sell_1_amount[1]=float(row["filled_amount"])
                        sell_1.remove(row["order_id"])
                        continue
                    if row["order_id"] in self_unfilled_orders_sell.keys():
                        old_record=self_unfilled_orders_sell[row["order_id"]]
                        filled_amount=float(row["filled_amount"])
                        filled_cash_amount=float(row["filled_cash_amount"])
                        if filled_cash_amount>old_record["filled_cash_amount"]:
                            profits_+=filled_cash_amount-old_record["filled_cash_amount"]
                            count_+=filled_amount-old_record["filled_amount"]
                            easylog.debug("flag1:filled sell %s filled_cash_amount:%s old_filled_cash_amount:%s,profits_:%s,count_:%f", 
                                            row["order_id"], filled_cash_amount,old_record["filled_cash_amount"],profits_,count_)
                            self_sell_price_min=max(self_sell_price_min,float(row["price"]))
                            del self_unfilled_orders_sell[row["order_id"]]
                            delete_list(row["order_id"],self_unfilled_orders_sell_helper,1)
                else:
                    if row["order_id"] in buy_1:
                        buy_1.remove(row["order_id"])
                        buy_sell_1_cash_amount[0]= float(row["filled_cash_amount"])
                        buy_sell_1_amount[0]=float(row["filled_amount"])
                        if row["order_id"] in back_buy_orders:
                            del back_buy_orders[row["order_id"]]
                    if row["order_id"] in self_unfilled_orders_buy.keys():
                        old_record=self_unfilled_orders_buy[row["order_id"]]
                        filled_amount=float(row["filled_amount"])
                        filled_cash_amount=float(row["filled_cash_amount"])
                        if filled_cash_amount>old_record["filled_cash_amount"]:
                            spend_+=filled_cash_amount-old_record["filled_cash_amount"]
                            spend_c_+=filled_amount-old_record["filled_amount"]
                            easylog.debug("flag1:filled no-sell %s filled_cash_amount:%s old_filled_cash_amount:%s,spend_:%s,spend_c_:%f", 
                                                row["order_id"], filled_cash_amount,old_record["filled_cash_amount"],spend_,spend_c_)
                            self_buy_min=min(self_buy_min,float(row["price"]))
                            del self_unfilled_orders_buy[row["order_id"]]
                            delete_list(row["order_id"],self_unfilled_orders_buy_helper,1)

            depths = query_pair_book(TARGET_PID)
            buy_depth=depths["data"]["bids"]
            sell_depth=depths["data"]["asks"]
            sell_edge,self_edge,sum_d,under_v,D_l=calculate_depth(sell_depth)

            profits+=profits_
            count+=count_
            spend+=spend_
            spend_c+=spend_c_

            if buy_sell_1_cash_amount[0]>buy_sell_1_cash_amount[1]:
                spend+=buy_sell_1_cash_amount[0]-buy_sell_1_cash_amount[1]
                spend_c+=buy_sell_1_amount[0]-buy_sell_1_amount[1]
            else:
                profits+=buy_sell_1_cash_amount[1]-buy_sell_1_cash_amount[0]
                count+=buy_sell_1_amount[1]-buy_sell_1_amount[0]
            if buy_sell_1_cash_amount[0] != buy_sell_1_cash_amount[1]:
                easylog.info("buy_sell_1_cash_amount:%s buy_sell_1_amount:%s", buy_sell_1_cash_amount, buy_sell_1_amount)

            profits_and_spen = profits-spend
            count_and_spen_c = count-spend_c
            store_data(profits,count,spend,spend_c)

            if len(buy_depth)>0 and len(sell_depth)>0 and float(buy_depth[0]['price']) > float(sell_depth[0]['price']):
                easylog.error("WRONG depth occur:%s", depths)
                sys.exit(1)
            if len(sell_depth)>0 and len(D2)>0 and len(D2[0])>0 and float(sell_depth[0]['price']) < D2[0][1]:
                easylog.warning("WARNING depth occur: depths %s D1:%s D2:%s", depths, D1, D2)
                
            x=0
            if model==0:
                easylog.info("model==0 self_sell_price_min:%s D_l:%s", self_sell_price_min,D_l)
                U_value=max(self_sell_price_min,D_l)

            # self_buy_min 自我买单边
            # D1_l=0 自我挂卖单边
            # D_l=0 所有挂单边
            # self_buy_under_V=0
            # distance 距离差
            #被吃单判断 触发还可以更改一下

            if spend_c_>0:
                old_self_buy_min=self_buy_min
                #等待
                t_spend_c+=spend_c_
                t_spend+=spend_
                t_profits+=profits_
                t_profits_C+=count_
                time.sleep(hit_hour_max_check_wait_interval)
                continue
            if t_spend_c>0:
                
                if sell_edge<V_ and t_profits_C>0:
                    

                U_value=min(sell_edge,old_self_buy_min)

                tv=max(profits_and_spen,0)
                tc=max(count_and_spen_c,0)

                if profits_and_spen<0 or count_and_spen_c<0 :
                    tv=0
                    tc=0

                D1,D2=calculate_D1_D2(g_config['whole_depth_money'],tv,tc,U_value,V_)
                easylog.info("D1:%s D2:%s whole_depth_money:%s profits:%s count:%s spend:%s spend_c:%s old_self_buy_min:%s sell_edge:%s V_:%s",
                            D1,D2,g_config['whole_depth_money'],profits,count,spend,spend_c,old_self_buy_min,sell_edge,V_)
                time_circle=date2num(datetime.datetime.now())
                create_sell_orders(D1)
                create_buy_orders(D2)
                depths = query_pair_book(TARGET_PID)
                buy_depth=depths["data"]["bids"]
                sell_depth=depths["data"]["asks"]
                sell_edge,self_edge,sum_d,under_v,D_l=calculate_depth(sell_depth)
                # 判断 D1_l与D_l之间的关系
                if D1[0][1] <D_l:
                    #随机游走模式
                    model=2
                else:
                    #缓慢上买模式
                    model=1
                t_spend_c=0
                t_spend=0
                t_profits=0
                t_profits_C=0

            tp=date2num(datetime.datetime.now())
            if tp-time_circle > 1/96: 
                easylog.info("tp:%s time_circle:%s", tp, time_circle)
                update_D=True
                time_circle=tp

            if model==0 and profits_>0:
                U_value=self_sell_price_min

                easylog.info("model==0 and profits_>0 set update_D=True,self_sell_price_min:%s",self_sell_price_min)
                update_D=True

            if update_D:
                #U_value?
                tv=max(profits_and_spen,0)
                tc=max(count_and_spen_c,0)

                if profits_and_spen<0 or count_and_spen_c<0 :
                    tv=0
                    tc=0

                D1,D2=calculate_D1_D2(g_config['whole_depth_money'],tv,tc,U_value,V_)
                easylog.info("D1:%s D2:%s whole_depth_money':%s profits:%s count:%s spend:%s spend_c:%s U_value:%s V_:%s",
                            D1,D2,g_config['whole_depth_money'],profits,count,spend,spend_c,U_value,V_)
                create_sell_orders(D1)
                create_buy_orders(D2)
                time_circle=date2num(datetime.datetime.now())
                depths = query_pair_book(TARGET_PID)
                buy_depth=depths["data"]["bids"]   # all buy orders
                sell_depth=depths["data"]["asks"]  # all sell orders
                sell_edge,self_edge,sum_d,under_v,D_l=calculate_depth(sell_depth)
                update_D = False


            if model==1:
                easylog.debug("==1=> model1")
                if sum_d>0 and D1[0][1] >=D_l:
                    tm=date2num(datetime.datetime.now())
                    while tm>week_end:
                        week_end+=7
                        week_spend=0

                    while tm>day_end:
                        day_end+=1
                        day_spend=0

                    while tm>hour_end:
                        hour_end+=1/24
                        hour_spend=0

                    canspend=calculate_back_count(sum_d,g_config['week_max_buy'],g_config['day_max_buy'],g_config['hour_max_buy'],week_spend,day_spend,hour_spend)
                    easylog.info("week_end:%s,day_end:%s,hour_end:%s,tm:%s,canspend:%s,hour_spend:%s sum_d:%s hour_spend:%s week_spend:%s,day_spend:%s", 
                                        week_end,day_end,hour_end,tm,canspend,hour_spend,sum_d,hour_spend,week_spend,day_spend)
                    x=generate_buy(week_end,day_end,hour_end,tm,canspend,hour_spend,(g_config['buy1_sell1_interval_sec']+g_config['market_check_interval_sec'])/24/3600)

                    x_ = x
                    if x > 0:
                        under_v.sort(key=take_second)
                        if under_v[0][0]*under_v[0][1]<x:
                            countx=under_v[0][0]
                            x=under_v[0][1]
                        else:
                            countx=x/under_v[0][1]
                            x=under_v[0][1]
                        easylog.info("t3:sum_d:%s canspend:%s x_:%s  x:%s countx:%s under_v[0]:%s",
                        sum_d,canspend, x_,  x, countx, under_v[0])
                        create_buy_order((countx, x),True)
                        xc=x*countx
                        week_spend+=xc
                        day_spend+=xc
                        hour_spend+=xc
                        U_value=x

                        if g_config['buy1_sell1_interval_sec']>0:
                            time.sleep(g_config['buy1_sell1_interval_sec'])
                else:
                    model=2
            if model==2:
                easylog.debug("==2=> model2")
                #随机设置U_value 
                #判断结束条件
                if D1[0][1]>V_ and D_l>V_:
                    model=0
                    continue

                if sum_d>0:
                    model=1
                    continue
                #1.查看是否有人买我的卖单，并得出吃单位置
                #需要迅速上移U_value
                if self_sell_price_min>0:
                    U_value=self_sell_price_min
                    tv=max(profits_and_spen,0)
                    tc=max(count_and_spen_c,0)
    
                    if profits_and_spen<0 or count_and_spen_c<0 :
                        tv=0
                        tc=0
    
                    D1,D2=calculate_D1_D2(g_config['whole_depth_money'],tv,tc,U_value,V_)
                    easylog.info("model2: D1:%s D2:%s whole_depth_money:%s profits:%s count:%s old_self_buy_min:%s sell_edge:%s V_:%s",
                                D1,D2,g_config['whole_depth_money'],profits,count,old_self_buy_min,sell_edge,V_)
                    time_circle=date2num(datetime.datetime.now())
                    create_sell_orders(D1)
                    create_buy_orders(D2)
                    continue

                db_max=0
                for item in buy_depth:
                    dprice=float(item["price"])
                    db_max=max(db_max,dprice)
                if db_max>D1[0][1]:
                    easylog.error("db_max>D1[0][1]: %s > %s",db_max,D1[0][1])

                l=min(old_self_buy_min,db_max)
                U_value=l+(D1[0][1]-l)*random_beta(7,3)
                x=0

            countx=0

            if x==0:
                easylog.debug("model = %d", model)
                tm=date2num(datetime.datetime.now())
                x=generate_k_line(min(U_value,D1[0][1]),D2[0][1],tm,old_x)
                old_x=x
                countx=(g_config['buy1_sell1_interval_sec']+g_config['market_check_interval_sec'])*g_config['oneday_min_volume']/(min(U_value,D1[0][1])*0.98)/600
                if days[2]>0 and mins_1[2]>0:
                    tmv=mins_1[2]/days[2]
                else:
                    tmv=1/144*(0.6+random.random())

                if tmv>1/70:
                    tmv=1/70.0*(0.5+0.5*random.random())
                countx*=tmv
                countx=countx*(0.2+random.random()*0.8)
                easylog.info("countx:%s  x:%s U_value:%s tmv:%s", countx, x, U_value, tmv)
                x = round(x, PRICE_RNUM)
                buy_max,buy_count=calculate_buy_edge(buy_depth)
                normal=False
                if buy_max<x:
                    normal=True
                else:
                    tr=random.random()
                    if tr<0.08:
                        if buy_count < MIN_TOKEN_AMOUNT:
                            buy_count = MIN_TOKEN_AMOUNT
                        create_sell_order((buy_count, buy_max))
                    elif tr<0.2:
                        buy_count_tp = buy_count*(0.1+random.random()*0.9)
                        if buy_count_tp < MIN_TOKEN_AMOUNT:
                            buy_count_tp = buy_count
                        create_sell_order((buy_count_tp, buy_max))
                    else:
                        normal=True
                        ds=D_l-buy_max
                        if ds>0:
                            x=buy_max+ds*random_beta(0.08,11)
                            x = round(x, PRICE_RNUM)
                    easylog.debug("normal:%s tr:%f ds:%s buy_max:%s x:%s", normal, tr, D_l-buy_max, buy_max, x)

                if normal:
                    if countx < MIN_TOKEN_AMOUNT:
                        countx = MIN_TOKEN_AMOUNT * (1+random.random())
                    rrt=random.random()
                    if rrt<0.5:
                        create_buy_order((countx, x))
                        if g_config['buy1_sell1_interval_sec']>0:
                            time.sleep(g_config['buy1_sell1_interval_sec'])
                        create_sell_order((countx, x))
                    else:
                        create_sell_order((countx, x))
                        if g_config['buy1_sell1_interval_sec']>0:
                            time.sleep(g_config['buy1_sell1_interval_sec'])
                        create_buy_order((countx, x))

            time.sleep(g_config['market_check_interval_sec']) 
        except Exception as ee:
            try:
                easylog.exception("Exception:%s", ee)
                send_dingding_msg("HooRobot restarted Exception:"+str(ee), g_config['dding_atoken'])
                store_data(profits,count,spend,spend_c)
                D1=[]
                D2=[]
                old_x=0
                back_start_time=0
                analyse_circle_startime=0
                self_buy_delimited=0
                back_pow=0
                profits=0
                count=0
                spend=0
                spend_c=0
                jd=load_data()
                if jd:
                    profits=jd["profits"]
                    count=jd["count"]
                    spend=jd["spend"]
                    spend_c=jd["spend_c"]

                time_circle=date2num(datetime.datetime.now())
                up_count=0
                up_spend_c=0
                #U_value=0.04
                #V_=0.005633
                U_value= 1.02 * V_
                tm=date2num(datetime.datetime.now())
                week_end=tm+7
                day_end=tm+1
                hour_end=tm+1/24
                week_spend=0
                day_spend=0
                hour_spend=0
                old_self_sell_max=10000
                old_self_buy_min=sys.maxsize
                t_spend_c=0
                t_spend=0
                t_profits=0
                t_profits_C=0
                hit_hour_max_check_wait_interval=2.0
                D1_l=0
                D_l=0
                self_buy_under_V=0
                distance=0
                model=0
                update_D=True
                setup_env()
            except Exception as eee:
                easylog.exception("Exception:%s", eee)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--conf", help="the config file path", )
    parser.add_argument("-d", "--debug", help='debug mode enable', default=False, action="store_true")
    args = parser.parse_args()

    if not args.conf:
        parser.print_usage()
        sys.exit(1)

    if args.debug:
        easylog.setup("./hoolog.out", level='debug', log_file_enable=False)
    else:
        easylog.setup("./hoolog.out", level='info', log_file_enable=True)
    easylog.info("Start to Rock")

    signal.signal(signal.SIGUSR1, reload_robot_config)  
    main_process(args.conf)